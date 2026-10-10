package main

import (
	"bytes"
	"crypto/md5"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func testService(t *testing.T, handler http.HandlerFunc) (*service, *httptest.Server) {
	t.Helper()
	up := httptest.NewServer(handler)
	t.Cleanup(up.Close)
	s, err := newService(Config{ServerURL: up.URL + "/music/", Username: "alice", Password: "a secret with 日本語", AllowHTTP: true})
	if err != nil {
		t.Fatal(err)
	}
	return s, up
}
func localRequest(s *service, method, endpoint, body string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, "http://"+listenAddr+endpoint, strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	return w
}
func authCheck(t *testing.T, r *http.Request) string {
	t.Helper()
	q := r.URL.Query()
	salt := q.Get("s")
	want := md5.Sum([]byte("a secret with 日本語" + salt))
	if len(salt) != 32 || q.Get("t") != hex.EncodeToString(want[:]) || q.Get("u") != "alice" || q.Get("p") != "" || q.Get("v") != "1.16.1" || q.Get("c") != "ShanlingNavidrome" || q.Get("f") != "json" {
		t.Errorf("invalid authentication parameters")
	}
	if !strings.HasPrefix(r.URL.Path, "/music/rest/") {
		t.Errorf("base path lost: %s", r.URL.Path)
	}
	return salt
}
func reply(w http.ResponseWriter, part string) {
	w.Header().Set("Content-Type", "application/json")
	_, _ = io.WriteString(w, `{"subsonic-response":{"status":"ok",`+part+`}}`)
}

func TestAuthenticatedUnicodeSearchPaging(t *testing.T) {
	salts := map[string]bool{}
	var mu sync.Mutex
	s, _ := testService(t, func(w http.ResponseWriter, r *http.Request) {
		salt := authCheck(t, r)
		mu.Lock()
		if salts[salt] {
			t.Error("salt reused")
		}
		salts[salt] = true
		mu.Unlock()
		if r.URL.Path != "/music/rest/search3.view" {
			t.Errorf("wrong endpoint: %s", r.URL.Path)
		}
		q := r.URL.Query()
		if q.Get("query") != "宇多田 光 & café" {
			t.Error("unicode query not preserved")
		}
		for _, k := range []string{"artistOffset", "albumOffset", "songOffset"} {
			if q.Get(k) != "15" {
				t.Errorf("bad %s", k)
			}
		}
		for _, k := range []string{"artistCount", "albumCount", "songCount"} {
			if q.Get(k) != "7" {
				t.Errorf("bad %s", k)
			}
		}
		reply(w, `"searchResult3":{"artist":[{"id":"ar1","name":"宇多田","albumCount":2}],"album":[{"id":"al1","name":"café","artistId":"ar1","songCount":3}],"song":[{"id":"s1","title":"光","discNumber":2,"contentType":"audio/flac"}]}`)
	})
	endpoint := "/v1/search?q=" + url.QueryEscape("宇多田 光 & café") + "&offset=15&size=7"
	for i := 0; i < 2; i++ {
		w := localRequest(s, "GET", endpoint, "")
		if w.Code != 200 {
			t.Fatalf("status %d: %s", w.Code, w.Body)
		}
		var v struct {
			Artists      []Artist
			Albums       []Album
			Tracks       []Track
			Offset, Size int
		}
		if json.Unmarshal(w.Body.Bytes(), &v) != nil {
			t.Fatal("bad UI JSON")
		}
		if len(v.Artists) != 1 || v.Artists[0].AlbumCount != 2 || v.Albums[0].ArtistID != "ar1" || v.Tracks[0].Disc != 2 || v.Tracks[0].ContentType != "audio/flac" || v.Offset != 15 || v.Size != 7 {
			t.Errorf("normalization failed: %s", w.Body)
		}
	}
}

func TestBrowsingEndpoints(t *testing.T) {
	s, _ := testService(t, func(w http.ResponseWriter, r *http.Request) {
		authCheck(t, r)
		switch r.URL.Path {
		case "/music/rest/getArtists.view":
			reply(w, `"artists":{"index":[{"name":"A","artist":[{"id":"ar","name":"Artist","albumCount":1}]}]}`)
		case "/music/rest/getArtist.view":
			if r.URL.Query().Get("id") != "ar" {
				t.Error("artist id missing")
			}
			reply(w, `"artist":{"id":"ar","name":"Artist","album":[{"id":"al","name":"Album"}]}`)
		case "/music/rest/getAlbumList2.view":
			q := r.URL.Query()
			if q.Get("type") != "alphabeticalByName" || q.Get("offset") != "60" || q.Get("size") != "30" {
				t.Error("bad album pagination")
			}
			reply(w, `"albumList2":{"album":[{"id":"al","name":"Album"}]}`)
		case "/music/rest/getAlbum.view":
			if r.URL.Query().Get("id") != "al" {
				t.Error("album id missing")
			}
			reply(w, `"album":{"id":"al","name":"Album","song":[{"id":"s","title":"Song","track":1}]}`)
		case "/music/rest/getPlaylists.view":
			reply(w, `"playlists":{"playlist":[{"id":"pl","name":"Mix","songCount":1}]}`)
		case "/music/rest/getPlaylist.view":
			reply(w, `"playlist":{"id":"pl","name":"Mix","entry":[{"id":"s","title":"Song"}]}`)
		default:
			t.Errorf("unexpected endpoint: %s", r.URL.Path)
			http.NotFound(w, r)
		}
	})
	for _, tc := range []struct{ path, want string }{
		{"/v1/artists", `"artists":[{"id":"ar"`}, {"/v1/artist?id=ar", `"albums":[{"id":"al"`},
		{"/v1/albums?offset=60", `"offset":60`}, {"/v1/album?id=al", `"tracks":[{"id":"s"`},
		{"/v1/playlists", `"song_count":1`}, {"/v1/playlist?id=pl", `"tracks":[{"id":"s"`},
	} {
		t.Run(tc.path, func(t *testing.T) {
			w := localRequest(s, "GET", tc.path, "")
			if w.Code != 200 || !strings.Contains(w.Body.String(), tc.want) {
				t.Fatalf("%d %s", w.Code, w.Body)
			}
		})
	}
}

func TestEmptyArraysAndValidation(t *testing.T) {
	calls := 0
	s, _ := testService(t, func(w http.ResponseWriter, r *http.Request) { calls++; reply(w, `"searchResult3":{}`) })
	w := localRequest(s, "GET", "/v1/search?q=none", "")
	if !strings.Contains(w.Body.String(), `"artists":[]`) || !strings.Contains(w.Body.String(), `"tracks":[]`) {
		t.Fatal(w.Body)
	}
	for _, p := range []string{"/v1/album", "/v1/artist?id=%0A", "/v1/search?size=0", "/v1/search?size=101", "/v1/albums?offset=-1", "/v1/albums?offset=1000001", "/v1/albums?size=nope"} {
		if w := localRequest(s, "GET", p, ""); w.Code != 400 {
			t.Fatalf("%s: %d", p, w.Code)
		}
	}
	if calls != 1 {
		t.Fatal("invalid inputs contacted upstream")
	}
}

func TestUpstreamFailuresRedactCredentials(t *testing.T) {
	for _, tc := range []struct {
		name, body   string
		status, want int
	}{
		{"auth", `{"subsonic-response":{"status":"failed","error":{"code":40,"message":"password=a secret with 日本語"}}}`, 200, 401},
		{"http", "secret", 503, 502}, {"invalid", "not JSON secret", 200, 502},
		{"missing status", `{}`, 200, 502}, {"too large", strings.Repeat("x", maxJSON+1), 200, 502},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s, _ := testService(t, func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(tc.status)
				_, _ = io.WriteString(w, tc.body)
			})
			w := localRequest(s, "GET", "/v1/artists", "")
			if w.Code != tc.want || strings.Contains(w.Body.String(), "secret") || strings.Contains(w.Body.String(), "日本語") {
				t.Fatalf("unsafe failure %d %s", w.Code, w.Body)
			}
		})
	}
}

func preparedURL(t *testing.T, s *service) string {
	t.Helper()
	w := localRequest(s, "POST", "/v1/prepare", `{"id":"song 日本語"}`)
	if w.Code != 200 {
		t.Fatal(w.Body)
	}
	var v struct{ URL string }
	if json.Unmarshal(w.Body.Bytes(), &v) != nil {
		t.Fatal("prepare JSON")
	}
	u, err := url.Parse(v.URL)
	if err != nil || u.Host != listenAddr || strings.Contains(v.URL, "song") || len(strings.TrimPrefix(u.Path, "/stream/")) != 32 {
		t.Fatalf("unsafe capability %q", v.URL)
	}
	return u.Path
}

func TestRangeAndHeadStreaming(t *testing.T) {
	payload := []byte("0123456789")
	s, _ := testService(t, func(w http.ResponseWriter, r *http.Request) {
		authCheck(t, r)
		if r.URL.Path != "/music/rest/stream.view" || r.URL.Query().Get("id") != "song 日本語" || r.URL.Query().Get("format") != "raw" {
			t.Error("bad stream request")
		}
		if r.Header.Get("Range") != "bytes=3-6" || r.Header.Get("If-Range") != `"etag"` {
			t.Error("range headers missing")
		}
		w.Header().Set("Content-Type", "audio/flac")
		w.Header().Set("ETag", `"etag"`)
		http.ServeContent(w, r, "song.flac", time.Time{}, bytes.NewReader(payload))
	})
	endpoint := preparedURL(t, s)
	for _, method := range []string{"GET", "HEAD"} {
		r := httptest.NewRequest(method, "http://"+listenAddr+endpoint, nil)
		r.Header.Set("Range", "bytes=3-6")
		r.Header.Set("If-Range", `"etag"`)
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		if w.Code != 206 || w.Header().Get("Content-Range") != "bytes 3-6/10" || w.Header().Get("Content-Type") != "audio/flac" || w.Header().Get("Content-Length") != "4" {
			t.Fatalf("range failed %d %v", w.Code, w.Header())
		}
		if (method == "GET" && w.Body.String() != "3456") || (method == "HEAD" && w.Body.Len() != 0) {
			t.Fatal("incorrect stream body")
		}
	}
}

func TestStreamCapabilitiesAndErrors(t *testing.T) {
	s, _ := testService(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/xml")
		_, _ = io.WriteString(w, `<error>secret</error>`)
	})
	endpoint := preparedURL(t, s)
	if w := localRequest(s, "GET", endpoint, ""); w.Code != 502 || strings.Contains(w.Body.String(), "secret") {
		t.Fatal("error document passed to player")
	}
	s.now = func() time.Time { return time.Now().Add(13 * time.Hour) }
	if w := localRequest(s, "GET", endpoint, ""); w.Code != 404 {
		t.Fatal("expired token accepted")
	}
	if w := localRequest(s, "GET", "/stream/guess", ""); w.Code != 404 {
		t.Fatal("unknown token accepted")
	}
	if w := localRequest(s, "POST", "/v1/prepare", `{"id":"x","password":"wrong"}`); w.Code != 400 {
		t.Fatal("unknown setup input accepted")
	}
	if w := localRequest(s, "POST", "/v1/prepare", `{"id":"x"} {}`); w.Code != 400 {
		t.Fatal("multiple JSON objects accepted")
	}
}

func TestUnsatisfiableRange(t *testing.T) {
	s, _ := testService(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Range", "bytes */10")
		w.Header().Set("Content-Length", "20")
		w.WriteHeader(416)
		_, _ = io.WriteString(w, "unsafe error message")
	})
	w := localRequest(s, "GET", preparedURL(t, s), "")
	if w.Code != 416 || w.Header().Get("Content-Range") != "bytes */10" || w.Header().Get("Content-Length") != "" || w.Body.Len() != 0 {
		t.Fatalf("bad unsatisfiable range response: %d %v %s", w.Code, w.Header(), w.Body)
	}
}

func TestStreamsBeforeUpstreamCompletes(t *testing.T) {
	release := make(chan struct{})
	s, _ := testService(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "audio/flac")
		_, _ = w.Write(bytes.Repeat([]byte("x"), 128*1024))
		w.(http.Flusher).Flush()
		<-release
		_, _ = io.WriteString(w, "end")
	})
	defer close(release)
	local := httptest.NewServer(s)
	defer local.Close()
	req, err := http.NewRequest("GET", local.URL+preparedURL(t, s), nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Host = listenAddr
	client := &http.Client{Timeout: 3 * time.Second}
	res, err := client.Do(req)
	if err != nil {
		t.Fatalf("proxy buffered instead of streaming: %v", err)
	}
	defer res.Body.Close()
	buf := make([]byte, 1024)
	if _, err := io.ReadFull(res.Body, buf); err != nil || !bytes.Equal(buf, bytes.Repeat([]byte("x"), 1024)) {
		t.Fatal("did not receive live stream chunk")
	}
}

func TestSessionBound(t *testing.T) {
	s, _ := testService(t, func(w http.ResponseWriter, r *http.Request) { t.Error("prepare should not contact upstream") })
	for i := 0; i < maxSessions+5; i++ {
		preparedURL(t, s)
	}
	if len(s.sessions) != maxSessions {
		t.Fatalf("unbounded sessions: %d", len(s.sessions))
	}
}

func TestCrossOriginRedirectDoesNotLeak(t *testing.T) {
	leaked := false
	evil := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { leaked = true }))
	defer evil.Close()
	s, _ := testService(t, func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, evil.URL+"/?t=do-not-follow", http.StatusFound)
	})
	if w := localRequest(s, "GET", "/v1/artists", ""); w.Code != 502 {
		t.Fatal("redirect accepted")
	}
	if w := localRequest(s, "GET", preparedURL(t, s), ""); w.Code != 502 {
		t.Fatal("media redirect accepted")
	}
	if leaked {
		t.Fatal("credentials leaked to foreign origin")
	}
}

func TestLoopbackOriginAndTLS(t *testing.T) {
	s, _ := testService(t, func(w http.ResponseWriter, r *http.Request) { reply(w, `"artists":{}`) })
	for _, tc := range []struct{ host, origin string }{{"evil.example:17831", ""}, {listenAddr, "http://evil.example"}} {
		r := httptest.NewRequest("GET", "http://"+tc.host+"/v1/status", nil)
		r.Header.Set("Origin", tc.origin)
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		if w.Code != 403 {
			t.Error("unsafe browser request allowed")
		}
	}
	tlsServer := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { reply(w, `"artists":{}`) }))
	defer tlsServer.Close()
	verified, err := newService(Config{ServerURL: tlsServer.URL, Username: "a", Password: "b"})
	if err != nil {
		t.Fatal(err)
	}
	if w := localRequest(verified, "GET", "/v1/artists", ""); w.Code != 502 {
		t.Fatal("untrusted TLS certificate accepted")
	}
	caPath := filepath.Join(t.TempDir(), "server.pem")
	certPEM := "-----BEGIN CERTIFICATE-----\n" + base64.StdEncoding.EncodeToString(tlsServer.Certificate().Raw) + "\n-----END CERTIFICATE-----\n"
	if err := os.WriteFile(caPath, []byte(certPEM), 0600); err != nil {
		t.Fatal(err)
	}
	trusted, err := newService(Config{ServerURL: tlsServer.URL, Username: "a", Password: "b", CAFile: caPath})
	if err != nil {
		t.Fatal(err)
	}
	if w := localRequest(trusted, "GET", "/v1/artists", ""); w.Code != 200 {
		t.Fatalf("custom CA failed: %d %s", w.Code, w.Body)
	}
	for _, cfg := range []Config{{ServerURL: "http://example.com", Username: "a", Password: "b"}, {ServerURL: "https://u:p@example.com", Username: "a", Password: "b"}, {ServerURL: "file:///tmp/x", Username: "a", Password: "b"}, {ServerURL: "https://example.com?token=secret", Username: "a", Password: "b"}} {
		if _, err := newService(cfg); err == nil {
			t.Error("invalid config accepted")
		}
	}
}

func TestDisplayTextComposedToNFC(t *testing.T) {
	cases := map[string]string{
		"Tiếng Việt": "Tiếng Việt",
		"Người":       "Người",
		"Quảng Ngãi":   "Quảng Ngãi",
		"ễ ́x":         "ễ ́x",
		"宇多田 café":       "宇多田 café",
		"a͠b":            "a͠b",
	}
	for in, want := range cases {
		if got := nfc(in); got != want {
			t.Errorf("nfc(%q) = %q, want %q", in, got, want)
		}
	}
	s, _ := testService(t, func(w http.ResponseWriter, r *http.Request) {
		reply(w, `"album":{"id":"Việt","name":"Việt","artist":"Sơn","song":[{"id":"s","title":"Hồng"}]}`)
	})
	body := localRequest(s, http.MethodGet, "/v1/album?id=x", "").Body.String()
	for _, want := range []string{"\"name\":\"Việt\"", "\"artist\":\"Sơn\"", "\"title\":\"Hồng\"", "\"id\":\"Việt\""} {
		if !strings.Contains(body, want) {
			t.Fatalf("display text not composed or ID altered, missing %q in %s", want, body)
		}
	}
}

func TestBatchPrepare(t *testing.T) {
	var mu sync.Mutex
	seen := map[string]bool{}
	s, _ := testService(t, func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		seen[r.URL.Query().Get("id")] = true
		mu.Unlock()
		w.Header().Set("Content-Type", "audio/flac")
		_, _ = io.WriteString(w, r.URL.Query().Get("id"))
	})
	w := localRequest(s, http.MethodPost, "/v1/prepare", `{"ids":["a","b","c"]}`)
	var out struct {
		URLs []string `json:"urls"`
	}
	if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &out) != nil || len(out.URLs) != 3 {
		t.Fatalf("batch prepare: %d %s", w.Code, w.Body)
	}
	for i, want := range []string{"a", "b", "c"} {
		if got := localRequest(s, http.MethodGet, strings.TrimPrefix(out.URLs[i], "http://"+listenAddr), "").Body.String(); got != want {
			t.Fatalf("url %d streams %q, want %q", i, got, want)
		}
	}
	many := make([]string, 129)
	for i := range many {
		many[i] = `"x"`
	}
	for _, body := range []string{`{"ids":[]}`, `{"id":"a","ids":["b"]}`, `{"ids":["ok","bad\u0001"]}`, `{"ids":[` + strings.Join(many, ",") + `]}`, `{}`} {
		if w := localRequest(s, http.MethodPost, "/v1/prepare", body); w.Code != 400 {
			t.Errorf("accepted %s: %d", body[:min(len(body), 40)], w.Code)
		}
	}
}
