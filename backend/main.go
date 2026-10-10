// navidrome-m1plus is a loopback-only Subsonic browser and streaming proxy.
package main

import (
	"context"
	"crypto/md5" // Subsonic token authentication requires MD5(password + salt).
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const listenAddr = "127.0.0.1:17831"
const maxJSON = 8 << 20
const maxSessions = 256

type Config struct {
	ServerURL string `json:"server_url"`
	Username  string `json:"username"`
	Password  string `json:"password"`
	AllowHTTP bool   `json:"allow_http"`
	CAFile    string `json:"ca_file,omitempty"`
	// Offline downloads: folder, free-space reserve and optional transcoding (empty = original files).
	OfflineDir        string `json:"offline_dir,omitempty"`
	OfflineReserveMB  int    `json:"offline_reserve_mb,omitempty"`
	OfflineFormat     string `json:"offline_format,omitempty"`
	OfflineMaxBitRate int    `json:"offline_max_bitrate,omitempty"`
}

type Artist struct {
	ID         string `json:"id"`
	Name       string `json:"name"`
	AlbumCount int    `json:"album_count"`
}
type Album struct {
	ID        string `json:"id"`
	Name      string `json:"name"`
	Artist    string `json:"artist"`
	ArtistID  string `json:"artist_id"`
	SongCount int    `json:"song_count"`
	Year      int    `json:"year"`
	Duration  int    `json:"duration"`
}
type Track struct {
	ID          string `json:"id"`
	Title       string `json:"title"`
	Artist      string `json:"artist"`
	Album       string `json:"album"`
	ArtistID    string `json:"artist_id"`
	AlbumID     string `json:"album_id"`
	Duration    int    `json:"duration"`
	Track       int    `json:"track"`
	Disc        int    `json:"disc"`
	ContentType string `json:"content_type"`
	Suffix      string `json:"suffix"`
	Size        int64  `json:"size"`
	Downloaded  bool   `json:"downloaded"`
}
type Playlist struct {
	ID        string `json:"id"`
	Name      string `json:"name"`
	SongCount int    `json:"song_count"`
	Duration  int    `json:"duration"`
}

// Wire structs are deliberately separate from the stable UI schema.
type wireArtist struct {
	ID, Name   string
	AlbumCount int
	Album      []wireAlbum
}
type wireAlbum struct {
	ID, Name, Artist, ArtistID string
	SongCount, Year, Duration  int
	Song                       []wireTrack
}
type wireTrack struct {
	ID, Title, Artist, Album, ArtistID, AlbumID string
	Duration, Track, DiscNumber, Year           int
	ContentType, Suffix, CoverArt               string
	DisplayAlbumArtist                          string
	Size                                        int64
}
type wirePlaylist struct {
	ID, Name            string
	SongCount, Duration int
	Entry               []wireTrack
}
type envelope struct {
	Response struct {
		Status string
		Error  *struct {
			Code    int
			Message string
		}
		Artists struct {
			Index []struct{ Artist []wireArtist }
		}
		Artist        wireArtist
		Album         wireAlbum
		AlbumList2    struct{ Album []wireAlbum }
		SearchResult3 struct {
			Artist []wireArtist
			Album  []wireAlbum
			Song   []wireTrack
		}
		Playlists struct{ Playlist []wirePlaylist }
		Playlist  wirePlaylist
	} `json:"subsonic-response"`
}

// Display text is composed to NFC; IDs stay byte-for-byte opaque.
func artist(w wireArtist) Artist { return Artist{w.ID, nfc(w.Name), w.AlbumCount} }
func album(w wireAlbum) Album {
	return Album{w.ID, nfc(w.Name), nfc(w.Artist), w.ArtistID, w.SongCount, w.Year, w.Duration}
}
func track(w wireTrack) Track {
	return Track{w.ID, nfc(w.Title), nfc(w.Artist), nfc(w.Album), w.ArtistID, w.AlbumID, w.Duration, w.Track, w.DiscNumber, w.ContentType, w.Suffix, w.Size, false}
}
func playlist(w wirePlaylist) Playlist { return Playlist{w.ID, nfc(w.Name), w.SongCount, w.Duration} }
func artists(ws []wireArtist) []Artist {
	a := make([]Artist, 0, len(ws))
	for _, w := range ws {
		a = append(a, artist(w))
	}
	return a
}
func albums(ws []wireAlbum) []Album {
	a := make([]Album, 0, len(ws))
	for _, w := range ws {
		a = append(a, album(w))
	}
	return a
}
func tracks(ws []wireTrack) []Track {
	a := make([]Track, 0, len(ws))
	for _, w := range ws {
		a = append(a, track(w))
	}
	return a
}

type capability struct {
	id               string
	expires, created time.Time
}
type service struct {
	cfg        Config
	base       *url.URL
	api, media *http.Client
	mu         sync.Mutex
	sessions   map[string]capability
	now        func() time.Time
	ui         http.Handler
	offline    *offline
}

func newService(cfg Config) (*service, error) {
	base, err := url.Parse(cfg.ServerURL)
	if err != nil || base.Hostname() == "" || (base.Scheme != "http" && base.Scheme != "https") || base.User != nil || base.RawQuery != "" || base.Fragment != "" {
		return nil, errors.New("server_url must be an http(s) origin with optional base path and no credentials, query or fragment")
	}
	if base.Scheme == "http" && !cfg.AllowHTTP {
		return nil, errors.New("HTTP requires allow_http=true")
	}
	if cfg.Username == "" || cfg.Password == "" {
		return nil, errors.New("username and password are required")
	}
	if cfg.OfflineDir != "" && !filepath.IsAbs(cfg.OfflineDir) {
		return nil, errors.New("offline_dir must be an absolute path")
	}
	if f := cfg.OfflineFormat; len(f) > 8 || strings.Trim(f, "abcdefghijklmnopqrstuvwxyz0123456789") != "" {
		return nil, errors.New("offline_format must be a short lowercase format name such as mp3 or opus")
	}
	if cfg.OfflineReserveMB < 0 || cfg.OfflineMaxBitRate < 0 || cfg.OfflineMaxBitRate > 10000 {
		return nil, errors.New("offline_reserve_mb and offline_max_bitrate must be non-negative")
	}
	tlsConfig := &tls.Config{MinVersion: tls.VersionTLS12}
	if cfg.CAFile != "" {
		pem, err := os.ReadFile(cfg.CAFile)
		if err != nil {
			return nil, errors.New("cannot read ca_file")
		}
		roots, err := x509.SystemCertPool()
		if err != nil || roots == nil {
			roots = x509.NewCertPool()
		}
		if !roots.AppendCertsFromPEM(pem) {
			return nil, errors.New("ca_file contains no certificates")
		}
		tlsConfig.RootCAs = roots
	}
	transport := &http.Transport{Proxy: nil, DialContext: (&net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second}).DialContext, TLSClientConfig: tlsConfig, TLSHandshakeTimeout: 10 * time.Second, ResponseHeaderTimeout: 15 * time.Second, IdleConnTimeout: 60 * time.Second, MaxIdleConns: 8, MaxIdleConnsPerHost: 4, MaxConnsPerHost: 12, DisableCompression: true}
	redirect := func(req *http.Request, via []*http.Request) error {
		if len(via) >= 4 {
			return errors.New("too many redirects")
		}
		// Never forward authenticated requests to another origin (including downgrade).
		if req.URL.Scheme != base.Scheme || !strings.EqualFold(req.URL.Host, base.Host) {
			return errors.New("cross-origin redirect refused")
		}
		return nil
	}
	s := &service{cfg: cfg, base: base, api: &http.Client{Transport: transport, Timeout: 20 * time.Second, CheckRedirect: redirect}, media: &http.Client{Transport: transport, CheckRedirect: redirect}, sessions: make(map[string]capability), now: time.Now}
	s.offline = newOffline(s, cfg)
	return s, nil
}

func loadConfig(filename string) (Config, error) {
	var c Config
	f, err := os.Open(filename)
	if err != nil {
		return c, errors.New("cannot open configuration")
	}
	defer f.Close()
	d := json.NewDecoder(io.LimitReader(f, 64*1024))
	d.DisallowUnknownFields()
	if err = d.Decode(&c); err != nil {
		return c, errors.New("invalid configuration JSON")
	}
	if d.Decode(&struct{}{}) != io.EOF {
		return c, errors.New("configuration must contain one JSON object")
	}
	return c, nil
}

func randomToken() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}
func (s *service) upstreamURL(endpoint string, params url.Values) (string, error) {
	salt, err := randomToken()
	if err != nil {
		return "", errors.New("random source unavailable")
	}
	hash := md5.Sum([]byte(s.cfg.Password + salt))
	u := *s.base
	u.Path = path.Join(s.base.Path, "rest", endpoint+".view")
	u.RawPath = ""
	q := url.Values{}
	for k, v := range params {
		q[k] = append([]string(nil), v...)
	}
	q.Set("u", s.cfg.Username)
	q.Set("t", hex.EncodeToString(hash[:]))
	q.Set("s", salt)
	q.Set("v", "1.16.1")
	q.Set("c", "ShanlingNavidrome")
	q.Set("f", "json")
	u.RawQuery = q.Encode()
	return u.String(), nil
}

type apiError struct {
	status        int
	code, message string
}

func (e *apiError) Error() string { return e.message }
func (s *service) fetch(ctx context.Context, endpoint string, params url.Values) (envelope, error) {
	var result envelope
	u, err := s.upstreamURL(endpoint, params)
	if err != nil {
		return result, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return result, errors.New("cannot create upstream request")
	}
	res, err := s.api.Do(req)
	if err != nil {
		return result, errors.New("server request failed; check network, URL and TLS trust")
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK {
		return result, &apiError{http.StatusBadGateway, "upstream_http", "server returned HTTP " + strconv.Itoa(res.StatusCode)}
	}
	data, err := io.ReadAll(io.LimitReader(res.Body, maxJSON+1))
	if err != nil {
		return result, errors.New("cannot read server response")
	}
	if len(data) > maxJSON {
		return result, errors.New("server response exceeds 8 MiB limit")
	}
	if json.Unmarshal(data, &result) != nil {
		return result, errors.New("server returned invalid JSON")
	}
	if result.Response.Status != "ok" {
		// Upstream messages can contain secrets/URLs; expose only the numeric code.
		code := 0
		if result.Response.Error != nil {
			code = result.Response.Error.Code
		}
		status := http.StatusBadGateway
		if code == 40 || code == 41 || code == 50 {
			status = http.StatusUnauthorized
		}
		return result, &apiError{status, "subsonic_error", "server rejected request (Subsonic code " + strconv.Itoa(code) + ")"}
	}
	return result, nil
}

func jsonResponse(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
func fail(w http.ResponseWriter, err error) {
	status, code, msg := http.StatusBadGateway, "upstream_error", err.Error()
	var a *apiError
	if errors.As(err, &a) {
		status, code, msg = a.status, a.code, a.message
	}
	jsonResponse(w, status, map[string]any{"error": map[string]string{"code": code, "message": msg}})
}
func bad(message string) error { return &apiError{http.StatusBadRequest, "invalid_request", message} }
func requestID(r *http.Request) (string, error) {
	id := r.URL.Query().Get("id")
	if !validID(id) {
		return "", bad("id is required (maximum 512 bytes, no control characters)")
	}
	return id, nil
}
func validID(id string) bool {
	if id == "" || len(id) > 512 {
		return false
	}
	for _, c := range id {
		if c < 32 || c == 127 {
			return false
		}
	}
	return true
}
func page(r *http.Request) (int, int, error) {
	offset, size := 0, 30
	var err error
	if v := r.URL.Query().Get("offset"); v != "" {
		offset, err = strconv.Atoi(v)
		if err != nil || offset < 0 || offset > 1000000 {
			return 0, 0, bad("offset must be between 0 and 1000000")
		}
	}
	if v := r.URL.Query().Get("size"); v != "" {
		size, err = strconv.Atoi(v)
		if err != nil || size < 1 || size > 100 {
			return 0, 0, bad("size must be between 1 and 100")
		}
	}
	return offset, size, nil
}

func (s *service) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// Bind loopback and reject DNS rebinding / cross-origin browser access.
	host := r.Host
	if host != listenAddr && host != "localhost:17831" {
		fail(w, &apiError{403, "forbidden", "loopback Host required"})
		return
	}
	if origin := r.Header.Get("Origin"); origin != "" && origin != "http://"+host {
		fail(w, &apiError{403, "forbidden", "same-origin requests required"})
		return
	}
	if strings.HasPrefix(r.URL.Path, "/stream/") {
		s.stream(w, r)
		return
	}
	if !strings.HasPrefix(r.URL.Path, "/v1/") {
		if s.ui != nil {
			s.ui.ServeHTTP(w, r)
		} else {
			http.NotFound(w, r)
		}
		return
	}
	if r.URL.Path == "/v1/prepare" {
		s.prepare(w, r)
		return
	}
	if strings.HasPrefix(r.URL.Path, "/v1/downloads") || r.URL.Path == "/v1/offline/remove" {
		s.downloads(w, r)
		return
	}
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", "GET")
		fail(w, &apiError{405, "method_not_allowed", "GET required"})
		return
	}
	if r.URL.Path == "/v1/status" {
		jsonResponse(w, 200, map[string]any{"configured": true, "server": s.base.Scheme + "://" + s.base.Host + s.base.Path, "allow_http": s.cfg.AllowHTTP, "version": "0.1.0"})
		return
	}
	params := url.Values{}
	var endpoint string
	var offset, size int
	var err error
	switch r.URL.Path {
	case "/v1/artists":
		endpoint = "getArtists"
	case "/v1/artist", "/v1/album", "/v1/playlist":
		var id string
		id, err = requestID(r)
		if err == nil {
			params.Set("id", id)
		}
		endpoint = map[string]string{"/v1/artist": "getArtist", "/v1/album": "getAlbum", "/v1/playlist": "getPlaylist"}[r.URL.Path]
	case "/v1/albums", "/v1/search":
		offset, size, err = page(r)
		if r.URL.Path == "/v1/albums" {
			endpoint = "getAlbumList2"
			params.Set("type", "alphabeticalByName")
			params.Set("offset", strconv.Itoa(offset))
			params.Set("size", strconv.Itoa(size))
		} else {
			endpoint = "search3"
			query := r.URL.Query().Get("q")
			if len(query) > 1024 {
				err = bad("search query exceeds 1024 bytes")
			}
			params.Set("query", query)
			for _, prefix := range []string{"artist", "album", "song"} {
				params.Set(prefix+"Count", strconv.Itoa(size))
				params.Set(prefix+"Offset", strconv.Itoa(offset))
			}
		}
	case "/v1/playlists":
		endpoint = "getPlaylists"
	default:
		http.NotFound(w, r)
		return
	}
	if err != nil {
		fail(w, err)
		return
	}
	result, err := s.fetch(r.Context(), endpoint, params)
	if err != nil {
		fail(w, err)
		return
	}
	v := result.Response
	var out any
	switch r.URL.Path {
	case "/v1/artists":
		a := []Artist{}
		for _, idx := range v.Artists.Index {
			a = append(a, artists(idx.Artist)...)
		}
		out = map[string]any{"artists": a}
	case "/v1/artist":
		out = map[string]any{"artist": artist(v.Artist), "albums": albums(v.Artist.Album)}
	case "/v1/albums":
		out = map[string]any{"albums": albums(v.AlbumList2.Album), "offset": offset, "size": size}
	case "/v1/album":
		out = s.withOffline("album", v.Album.ID, map[string]any{"album": album(v.Album)}, v.Album.Song)
	case "/v1/search":
		out = map[string]any{"artists": artists(v.SearchResult3.Artist), "albums": albums(v.SearchResult3.Album), "tracks": tracks(v.SearchResult3.Song), "offset": offset, "size": size}
	case "/v1/playlists":
		p := make([]Playlist, 0, len(v.Playlists.Playlist))
		for _, v := range v.Playlists.Playlist {
			p = append(p, playlist(v))
		}
		out = map[string]any{"playlists": p}
	case "/v1/playlist":
		out = s.withOffline("playlist", v.Playlist.ID, map[string]any{"playlist": playlist(v.Playlist)}, v.Playlist.Entry)
	}
	jsonResponse(w, 200, out)
}

// withOffline adds per-track "downloaded" flags and the collection's offline summary.
func (s *service) withOffline(kind, id string, out map[string]any, songs []wireTrack) map[string]any {
	sum, have := s.offline.summary(kind, id, songs)
	t := tracks(songs)
	for i := range t {
		t[i].Downloaded = have[t[i].ID]
	}
	out["tracks"], out["offline"] = t, sum
	return out
}

func (s *service) prepare(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", "POST")
		fail(w, &apiError{405, "method_not_allowed", "POST required"})
		return
	}
	if !strings.HasPrefix(strings.ToLower(r.Header.Get("Content-Type")), "application/json") {
		fail(w, bad("application/json required"))
		return
	}
	var input struct {
		ID string `json:"id"`
	}
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 2048))
	d.DisallowUnknownFields()
	if d.Decode(&input) != nil || !validID(input.ID) {
		fail(w, bad("valid song id required"))
		return
	}
	if d.Decode(&struct{}{}) != io.EOF {
		fail(w, bad("one JSON object required"))
		return
	}
	token, err := randomToken()
	if err != nil {
		fail(w, errors.New("random source unavailable"))
		return
	}
	now := s.now()
	s.mu.Lock()
	for k, v := range s.sessions {
		if !now.Before(v.expires) {
			delete(s.sessions, k)
		}
	}
	if len(s.sessions) >= maxSessions {
		var oldest string
		var at time.Time
		for k, v := range s.sessions {
			if oldest == "" || v.created.Before(at) {
				oldest, at = k, v.created
			}
		}
		delete(s.sessions, oldest)
	}
	s.sessions[token] = capability{input.ID, now.Add(12 * time.Hour), now}
	s.mu.Unlock()
	jsonResponse(w, 200, map[string]any{"url": "http://" + listenAddr + "/stream/" + token, "expires_in": 43200})
}

func (s *service) stream(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD")
		fail(w, &apiError{405, "method_not_allowed", "GET or HEAD required"})
		return
	}
	token := strings.TrimPrefix(r.URL.Path, "/stream/")
	s.mu.Lock()
	cap, ok := s.sessions[token]
	if ok && !s.now().Before(cap.expires) {
		delete(s.sessions, token)
		ok = false
	}
	s.mu.Unlock()
	if !ok {
		fail(w, &apiError{404, "invalid_stream", "stream session missing or expired"})
		return
	}
	u, err := s.upstreamURL("stream", url.Values{"id": {cap.id}, "format": {"raw"}})
	if err != nil {
		fail(w, err)
		return
	}
	req, err := http.NewRequestWithContext(r.Context(), r.Method, u, nil)
	if err != nil {
		fail(w, errors.New("cannot create stream request"))
		return
	}
	for _, h := range []string{"Range", "If-Range"} {
		if v := r.Header.Get(h); v != "" {
			req.Header.Set(h, v)
		}
	}
	req.Header.Set("Accept-Encoding", "identity")
	res, err := s.media.Do(req)
	if err != nil {
		fail(w, errors.New("stream request failed; check network and TLS trust"))
		return
	}
	defer res.Body.Close()
	// Subsonic errors may arrive with HTTP 200 as XML/JSON; never feed them to the decoder.
	ct := strings.ToLower(res.Header.Get("Content-Type"))
	if res.StatusCode == 200 && (strings.Contains(ct, "json") || strings.Contains(ct, "xml")) {
		fail(w, errors.New("server returned an error document instead of audio"))
		return
	}
	if res.StatusCode != 200 && res.StatusCode != 206 && res.StatusCode != 416 {
		fail(w, &apiError{502, "stream_error", "server returned HTTP " + strconv.Itoa(res.StatusCode)})
		return
	}
	for _, h := range []string{"Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "ETag", "Last-Modified"} {
		if res.StatusCode == http.StatusRequestedRangeNotSatisfiable && h == "Content-Length" {
			continue // Error bodies are suppressed, so their length must not be forwarded.
		}
		if v := res.Header.Get(h); v != "" {
			w.Header().Set(h, v)
		}
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(res.StatusCode)
	if r.Method != http.MethodHead && res.StatusCode != 416 {
		_, _ = io.Copy(w, res.Body)
	}
}

func main() {
	configFile := flag.String("config", "/mnt/mmc/navidrome/config.json", "configuration JSON path")
	uiDir := flag.String("ui-dir", "/mnt/mmc/navidrome/ui", "static browser UI directory (empty disables)")
	flag.Parse()
	cfg, err := loadConfig(*configFile)
	if err != nil {
		log.Print(err)
		os.Exit(1)
	}
	svc, err := newService(cfg)
	if err != nil {
		log.Print(err)
		os.Exit(1)
	}
	if *uiDir != "" {
		svc.ui = http.FileServer(http.Dir(*uiDir))
	}
	go svc.offline.run(context.Background())
	server := &http.Server{Addr: listenAddr, Handler: svc, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16 * 1024}
	// Stream responses intentionally have no global write timeout; context cancellation
	// closes upstream requests when the player disconnects.
	done := make(chan os.Signal, 1)
	signal.Notify(done, syscall.SIGTERM, syscall.SIGINT)
	go func() {
		<-done
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(ctx)
	}()
	fmt.Println("Navidrome service listening on http://" + listenAddr)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Print("local HTTP service failed")
		os.Exit(1)
	}
}
