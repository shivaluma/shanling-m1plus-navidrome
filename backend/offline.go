package main

// Offline copies of the user's own Navidrome library on the player's SD card.
//
// One background worker downloads album/playlist jobs a file at a time into
// <offline_dir>/<album artist>/<album (year)>/, using FAT32-safe names. Files
// are written to a hidden .part file, synced, size-checked and renamed, so the
// stock library scanner never indexes partial audio. A manifest records which
// files this feature created; removal deletes only those files.

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	"unicode/utf8"
)

const (
	defaultOfflineDir   = "/mnt/mmc/Navidrome"
	defaultReserveMB    = 500
	manifestName        = ".navidrome-offline.json"
	fatFileLimit        = 4<<30 - 1 // FAT32 per-file maximum
	maxJobs             = 30
	downloadIdleTimeout = 60 * time.Second
)

// errPermanent marks failures that retrying cannot fix.
type errPermanent struct{ msg string }

func (e *errPermanent) Error() string { return e.msg }

type offlineTrack struct {
	Path    string   `json:"path"` // relative to the offline root, slash-separated
	Size    int64    `json:"size"`
	AlbumID string   `json:"album_id,omitempty"`
	Owners  []string `json:"owners"` // "album:<id>" or "playlist:<id>"
}
type manifest struct {
	Version   int                      `json:"version"`
	Tracks    map[string]*offlineTrack `json:"tracks"`
	Covers    map[string]bool          `json:"covers"`    // relative cover.jpg paths created here
	Playlists map[string]string        `json:"playlists"` // playlist id -> relative .m3u8 path
	Queue     []string                 `json:"queue"`     // pending job keys, resumed after restart
}

// DownloadJob is the public job state returned by /v1/downloads.
type DownloadJob struct {
	ID         string `json:"id"`
	Kind       string `json:"kind"`
	SourceID   string `json:"source_id"`
	Name       string `json:"name"`
	State      string `json:"state"` // queued, active, done, failed, cancelled
	Total      int    `json:"total"`
	Done       int    `json:"done"`
	Bytes      int64  `json:"bytes"`
	TotalBytes int64  `json:"total_bytes"`
	Current    string `json:"current"`
	Error      string `json:"error"`
	cancel     context.CancelFunc
}

// OfflineSummary is attached to album and playlist responses.
type OfflineSummary struct {
	Downloaded int    `json:"downloaded"`
	Total      int    `json:"total"`
	State      string `json:"state"`
	Done       int    `json:"done"`
	Error      string `json:"error"`
}

type offline struct {
	svc       *service
	root      string
	reserve   int64
	format    string // empty: original files through "download"
	bitrate   int
	mu        sync.Mutex
	man       manifest
	jobs      []*DownloadJob
	wake      chan struct{}
	freeSpace func(string) (int64, error)
	retryWait func(int) time.Duration
}

func newOffline(svc *service, cfg Config) *offline {
	root := cfg.OfflineDir
	if root == "" {
		root = defaultOfflineDir
	}
	reserve := int64(cfg.OfflineReserveMB)
	if reserve <= 0 {
		reserve = defaultReserveMB
	}
	o := &offline{svc: svc, root: filepath.Clean(root), reserve: reserve << 20, format: cfg.OfflineFormat,
		bitrate: cfg.OfflineMaxBitRate, wake: make(chan struct{}, 1), freeSpace: diskFree,
		retryWait: func(attempt int) time.Duration {
			return []time.Duration{2 * time.Second, 5 * time.Second, 15 * time.Second}[attempt]
		}}
	o.man = o.loadManifest()
	for _, key := range o.man.Queue {
		if kind, id, ok := strings.Cut(key, ":"); ok && (kind == "album" || kind == "playlist") && validID(id) {
			o.jobs = append(o.jobs, &DownloadJob{ID: key, Kind: kind, SourceID: id, State: "queued"})
		}
	}
	return o
}

func diskFree(dir string) (int64, error) {
	var st syscall.Statfs_t
	if err := syscall.Statfs(dir, &st); err != nil {
		return 0, err
	}
	return int64(st.Bavail) * int64(st.Bsize), nil
}

func (o *offline) loadManifest() manifest {
	m := manifest{Version: 1, Tracks: map[string]*offlineTrack{}, Covers: map[string]bool{}, Playlists: map[string]string{}}
	data, err := os.ReadFile(filepath.Join(o.root, manifestName))
	if err != nil {
		return m
	}
	var loaded manifest
	if json.Unmarshal(data, &loaded) != nil || loaded.Version != 1 {
		// Keep the unreadable copy for inspection; never delete audio because of it.
		_ = os.Rename(filepath.Join(o.root, manifestName), filepath.Join(o.root, manifestName+".corrupt"))
		return m
	}
	for id, t := range loaded.Tracks {
		if t != nil && safeRel(t.Path) {
			m.Tracks[id] = t
		}
	}
	for p := range loaded.Covers {
		if safeRel(p) {
			m.Covers[p] = true
		}
	}
	for id, p := range loaded.Playlists {
		if safeRel(p) {
			m.Playlists[id] = p
		}
	}
	m.Queue = loaded.Queue
	return m
}

// saveManifest must be called with o.mu held.
func (o *offline) saveManifest() error {
	o.man.Queue = o.man.Queue[:0]
	for _, j := range o.jobs {
		if j.State == "queued" || j.State == "active" {
			o.man.Queue = append(o.man.Queue, j.ID)
		}
	}
	data, err := json.MarshalIndent(o.man, "", " ")
	if err != nil {
		return err
	}
	if err = os.MkdirAll(o.root, 0o755); err != nil {
		return err
	}
	return writeAtomic(filepath.Join(o.root, manifestName), data)
}

func writeAtomic(dest string, data []byte) error {
	tmp, err := os.CreateTemp(filepath.Dir(dest), "."+filepath.Base(dest)+"-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err = tmp.Write(data); err == nil {
		err = tmp.Sync()
	}
	if cerr := tmp.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		return err
	}
	return os.Rename(tmp.Name(), dest)
}

// safeRel accepts only clean, relative, slash-separated paths inside the root.
func safeRel(p string) bool {
	if p == "" || strings.HasPrefix(p, "/") || strings.Contains(p, "\\") {
		return false
	}
	for _, part := range strings.Split(p, "/") {
		if part == "" || part == "." || part == ".." {
			return false
		}
	}
	return true
}

// fatName makes one FAT32 long-filename component from display metadata.
func fatName(s string) string {
	var b strings.Builder
	for _, r := range nfc(s) {
		if r < 0x20 || r == 0x7f || strings.ContainsRune(`\/:*?"<>|`, r) {
			r = '_'
		}
		b.WriteRune(r)
	}
	name := strings.TrimLeft(b.String(), " ")
	if len(name) > 100 {
		cut := 100
		for cut > 0 && !utf8.RuneStart(name[cut]) {
			cut--
		}
		name = name[:cut]
	}
	name = strings.TrimRight(name, " .")
	if name == "" {
		return "Unknown"
	}
	return name
}

var contentTypeSuffix = map[string]string{
	"audio/flac": "flac", "audio/x-flac": "flac", "audio/mpeg": "mp3", "audio/mp3": "mp3",
	"audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac", "audio/ogg": "ogg",
	"audio/opus": "opus", "audio/wav": "wav", "audio/x-wav": "wav", "audio/x-aiff": "aiff",
	"audio/aiff": "aiff", "audio/x-ape": "ape", "audio/dsf": "dsf", "audio/x-dsf": "dsf",
	"audio/x-dff": "dff", "audio/x-ms-wma": "wma",
}

func fileSuffix(t wireTrack, format string) string {
	s := strings.ToLower(t.Suffix)
	if format != "" {
		s = strings.ToLower(format)
	}
	if s == "" {
		s = contentTypeSuffix[strings.ToLower(t.ContentType)]
	}
	if s == "" || len(s) > 8 || strings.Trim(s, "abcdefghijklmnopqrstuvwxyz0123456789") != "" {
		return "bin"
	}
	return s
}

// relPath is "<album artist>/<album (year)>/<[disc-]track title>.<suffix>".
func relPath(t wireTrack, albumArtist, album string, year int, format string) string {
	artist := albumArtist
	if artist == "" {
		artist = t.DisplayAlbumArtist
	}
	if artist == "" {
		artist = t.Artist
	}
	if album == "" {
		album = t.Album
	}
	if year == 0 {
		year = t.Year
	}
	albumDir := album
	if year > 0 {
		albumDir += " (" + strconv.Itoa(year) + ")"
	}
	title := t.Title
	if title == "" {
		title = "Untitled"
	}
	prefix := ""
	if t.Track > 0 {
		prefix = strconv.Itoa(t.Track)
		if t.Track < 10 {
			prefix = "0" + prefix
		}
		if t.DiscNumber > 1 {
			prefix = strconv.Itoa(t.DiscNumber) + "-" + prefix
		}
		prefix += " "
	}
	return fatName(artist) + "/" + fatName(albumDir) + "/" + fatName(prefix+title) + "." + fileSuffix(t, format)
}

func (o *offline) abs(rel string) string { return filepath.Join(o.root, filepath.FromSlash(rel)) }

// ---- Job queue. ----

func (o *offline) enqueue(kind, id string) *DownloadJob {
	key := kind + ":" + id
	o.mu.Lock()
	defer o.mu.Unlock()
	for _, j := range o.jobs {
		if j.ID == key {
			if j.State == "queued" || j.State == "active" {
				return j
			}
			j.State, j.Error, j.Done, j.Bytes, j.Current = "queued", "", 0, 0, ""
			o.moveToEnd(j)
			_ = o.saveManifest()
			o.signal()
			return j
		}
	}
	j := &DownloadJob{ID: key, Kind: kind, SourceID: id, State: "queued"}
	o.jobs = append(o.jobs, j)
	o.trimJobs()
	_ = o.saveManifest()
	o.signal()
	return j
}

func (o *offline) moveToEnd(j *DownloadJob) {
	for i, x := range o.jobs {
		if x == j {
			o.jobs = append(append(o.jobs[:i:i], o.jobs[i+1:]...), j)
			return
		}
	}
}

func (o *offline) trimJobs() {
	for len(o.jobs) > maxJobs {
		removed := false
		for i, j := range o.jobs {
			if j.State != "queued" && j.State != "active" {
				o.jobs = append(o.jobs[:i:i], o.jobs[i+1:]...)
				removed = true
				break
			}
		}
		if !removed {
			return
		}
	}
}

func (o *offline) signal() {
	select {
	case o.wake <- struct{}{}:
	default:
	}
}

func (o *offline) cancelJob(key string) bool {
	o.mu.Lock()
	defer o.mu.Unlock()
	for _, j := range o.jobs {
		if j.ID == key && (j.State == "queued" || j.State == "active") {
			if j.cancel != nil {
				j.cancel()
			}
			j.State, j.Current = "cancelled", ""
			_ = o.saveManifest()
			return true
		}
	}
	return false
}

func (o *offline) snapshot() []DownloadJob {
	o.mu.Lock()
	defer o.mu.Unlock()
	out := make([]DownloadJob, 0, len(o.jobs))
	for i := len(o.jobs) - 1; i >= 0; i-- {
		j := *o.jobs[i]
		j.cancel = nil
		out = append(out, j)
	}
	return out
}

// run processes queued jobs until ctx ends.
func (o *offline) run(ctx context.Context) {
	for {
		o.mu.Lock()
		var job *DownloadJob
		for _, j := range o.jobs {
			if j.State == "queued" {
				job = j
				break
			}
		}
		var jobCtx context.Context
		if job != nil {
			jobCtx, job.cancel = context.WithCancel(ctx)
			job.State = "active"
		}
		o.mu.Unlock()
		if job == nil {
			select {
			case <-ctx.Done():
				return
			case <-o.wake:
				continue
			}
		}
		err := o.process(jobCtx, job)
		o.mu.Lock()
		job.cancel()
		job.cancel, job.Current = nil, ""
		switch {
		case job.State == "cancelled":
		case err != nil:
			job.State, job.Error = "failed", err.Error()
		default:
			job.State = "done"
		}
		_ = o.saveManifest()
		o.mu.Unlock()
		if ctx.Err() != nil {
			return
		}
	}
}

func (o *offline) update(f func()) {
	o.mu.Lock()
	f()
	o.mu.Unlock()
}

func (o *offline) process(ctx context.Context, job *DownloadJob) error {
	var songs []wireTrack
	var albumArtist, albumName string
	var year int
	endpoint := map[string]string{"album": "getAlbum", "playlist": "getPlaylist"}[job.Kind]
	result, err := o.svc.fetch(ctx, endpoint, url.Values{"id": {job.SourceID}})
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return err
	}
	if job.Kind == "album" {
		a := result.Response.Album
		songs, albumArtist, albumName, year = a.Song, a.Artist, a.Name, a.Year
		o.update(func() { job.Name = nfc(a.Name) })
	} else {
		p := result.Response.Playlist
		songs = p.Entry
		o.update(func() { job.Name = nfc(p.Name) })
	}
	var total int64
	for _, s := range songs {
		total += s.Size
	}
	o.update(func() { job.Total, job.TotalBytes, job.Done, job.Bytes = len(songs), total, 0, 0 })
	if _, err := os.Stat(filepath.Dir(o.root)); err != nil {
		return &errPermanent{"SD card is not available"}
	}
	if err := os.MkdirAll(o.root, 0o755); err != nil {
		return &errPermanent{"cannot create the download folder"}
	}
	owner := job.ID
	var paths []string
	for _, s := range songs {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if !validID(s.ID) {
			continue
		}
		o.update(func() { job.Current = nfc(s.Title) })
		albumID := s.AlbumID
		if job.Kind == "album" && albumID == "" {
			albumID = job.SourceID
		}
		rel, err := o.downloadSong(ctx, s, albumArtist, albumName, year, albumID, owner, job)
		if err != nil {
			return err
		}
		paths = append(paths, rel)
		o.update(func() { job.Done++ })
	}
	if job.Kind == "playlist" {
		o.writePlaylist(job.SourceID, job.Name, paths)
	}
	return nil
}

// downloadSong returns the song's relative path after it is complete on disk.
func (o *offline) downloadSong(ctx context.Context, s wireTrack, albumArtist, albumName string, year int,
	albumID, owner string, job *DownloadJob) (string, error) {
	o.mu.Lock()
	existing := o.man.Tracks[s.ID]
	o.mu.Unlock()
	if existing != nil {
		if st, err := os.Stat(o.abs(existing.Path)); err == nil && (existing.Size == 0 || st.Size() == existing.Size) {
			o.addOwner(s.ID, owner)
			o.update(func() { job.Bytes += st.Size() })
			return existing.Path, nil
		}
	}
	if s.Size > fatFileLimit {
		return "", &errPermanent{"a file is larger than the SD card's 4 GB file limit"}
	}
	rel, err := o.reservePath(s.ID, relPath(s, albumArtist, albumName, year, o.format))
	if err != nil {
		return "", err
	}
	dest := o.abs(rel)
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return "", &errPermanent{"cannot create an album folder"}
	}
	free, err := o.freeSpace(o.root)
	if err == nil && free-o.reserve < s.Size {
		return "", &errPermanent{"not enough space on the SD card"}
	}
	var size int64
	for attempt := 0; ; attempt++ {
		size, err = o.fetchFile(ctx, s, dest, job)
		var permanent *errPermanent
		if err == nil || ctx.Err() != nil || errors.As(err, &permanent) || attempt == 3 {
			break
		}
		select {
		case <-ctx.Done():
		case <-time.After(o.retryWait(attempt)):
		}
	}
	if err != nil {
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
		return "", err
	}
	o.mu.Lock()
	// A cancel or removal may race the final rename; never leave an unrecorded file.
	if job.State != "active" {
		o.mu.Unlock()
		_ = os.Remove(dest)
		return "", context.Canceled
	}
	t := &offlineTrack{Path: rel, Size: size, AlbumID: albumID, Owners: []string{owner}}
	if old := o.man.Tracks[s.ID]; old != nil {
		t.Owners = appendUnique(old.Owners, owner)
	}
	o.man.Tracks[s.ID] = t
	err = o.saveManifest()
	o.mu.Unlock()
	if err != nil {
		return "", &errPermanent{"cannot write the download list"}
	}
	o.saveCover(ctx, s.CoverArt, filepath.ToSlash(filepath.Dir(filepath.FromSlash(rel))))
	return rel, nil
}

// reservePath never reuses a path owned by another song or a file this feature did not create.
func (o *offline) reservePath(id, rel string) (string, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if old := o.man.Tracks[id]; old != nil && old.Path == rel {
		return rel, nil
	}
	ext := filepath.Ext(rel)
	stem := strings.TrimSuffix(rel, ext)
	for n := 1; n < 100; n++ {
		candidate := rel
		if n > 1 {
			candidate = stem + " (" + strconv.Itoa(n) + ")" + ext
		}
		taken := false
		for other, t := range o.man.Tracks {
			if other != id && strings.EqualFold(t.Path, candidate) {
				taken = true
				break
			}
		}
		if _, err := os.Lstat(o.abs(candidate)); err == nil {
			taken = true
		}
		if !taken {
			return candidate, nil
		}
	}
	return "", &errPermanent{"too many files with the same name"}
}

func appendUnique(list []string, v string) []string {
	for _, x := range list {
		if x == v {
			return list
		}
	}
	return append(list, v)
}

func (o *offline) addOwner(id, owner string) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if t := o.man.Tracks[id]; t != nil {
		t.Owners = appendUnique(t.Owners, owner)
		_ = o.saveManifest()
	}
}

// idleReader restarts the stall timer whenever data arrives.
type idleReader struct {
	r     io.Reader
	timer *time.Timer
	idle  time.Duration
}

func (r *idleReader) Read(p []byte) (int, error) {
	n, err := r.r.Read(p)
	if n > 0 {
		r.timer.Reset(r.idle)
	}
	return n, err
}

func subsonicErrorMessage(body []byte) string {
	var env envelope
	if json.Unmarshal(body, &env) == nil && env.Response.Error != nil {
		switch env.Response.Error.Code {
		case 50:
			return "the server does not allow downloads for this account"
		case 70:
			return "a song was not found on the server"
		case 40, 41:
			return "the server rejected the login"
		}
		return "server error (Subsonic code " + strconv.Itoa(env.Response.Error.Code) + ")"
	}
	return "the server returned an error instead of audio"
}

func (o *offline) fetchFile(ctx context.Context, s wireTrack, dest string, job *DownloadJob) (int64, error) {
	part := filepath.Join(filepath.Dir(dest), "."+filepath.Base(dest)+".part")
	var offset int64
	if st, err := os.Stat(part); err == nil && o.format == "" && (s.Size == 0 || st.Size() < s.Size) {
		offset = st.Size()
	}
	params := url.Values{"id": {s.ID}}
	endpoint := "download"
	if o.format != "" {
		endpoint = "stream"
		params.Set("format", o.format)
		if o.bitrate > 0 {
			params.Set("maxBitRate", strconv.Itoa(o.bitrate))
		}
	}
	u, err := o.svc.upstreamURL(endpoint, params)
	if err != nil {
		return 0, err
	}
	reqCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	req, err := http.NewRequestWithContext(reqCtx, http.MethodGet, u, nil)
	if err != nil {
		return 0, errors.New("cannot create download request")
	}
	req.Header.Set("Accept-Encoding", "identity")
	if offset > 0 {
		req.Header.Set("Range", "bytes="+strconv.FormatInt(offset, 10)+"-")
	}
	res, err := o.svc.media.Do(req)
	if err != nil {
		return 0, errors.New("download failed; check Wi-Fi")
	}
	defer res.Body.Close()
	ct := strings.ToLower(res.Header.Get("Content-Type"))
	if strings.Contains(ct, "json") || strings.Contains(ct, "xml") {
		body, _ := io.ReadAll(io.LimitReader(res.Body, 64*1024))
		return 0, &errPermanent{subsonicErrorMessage(body)}
	}
	flags := os.O_CREATE | os.O_WRONLY
	switch {
	case res.StatusCode == http.StatusPartialContent && offset > 0 &&
		strings.HasPrefix(res.Header.Get("Content-Range"), "bytes "+strconv.FormatInt(offset, 10)+"-"):
		flags |= os.O_APPEND
	case res.StatusCode == http.StatusOK:
		flags |= os.O_TRUNC
		offset = 0
	case res.StatusCode == http.StatusRequestedRangeNotSatisfiable && offset > 0 && offset == s.Size:
		flags |= os.O_APPEND
		res.Body = http.NoBody
	case res.StatusCode == http.StatusForbidden || res.StatusCode == http.StatusUnauthorized:
		return 0, &errPermanent{"the server does not allow downloads for this account"}
	case res.StatusCode == http.StatusNotFound:
		return 0, &errPermanent{"a song was not found on the server"}
	default:
		_ = os.Remove(part)
		return 0, errors.New("server returned HTTP " + strconv.Itoa(res.StatusCode))
	}
	f, err := os.OpenFile(part, flags, 0o644)
	if err != nil {
		return 0, &errPermanent{"cannot write to the SD card"}
	}
	timer := time.AfterFunc(downloadIdleTimeout, cancel)
	defer timer.Stop()
	body := &idleReader{r: res.Body, timer: timer, idle: downloadIdleTimeout}
	written, copyErr := io.CopyBuffer(f, &progressReader{r: body, add: func(n int64) {
		o.update(func() { job.Bytes += n })
	}}, make([]byte, 64*1024))
	syncErr := f.Sync()
	closeErr := f.Close()
	if copyErr != nil {
		if errors.Is(copyErr, syscall.ENOSPC) {
			return 0, &errPermanent{"not enough space on the SD card"}
		}
		return 0, errors.New("download interrupted; check Wi-Fi")
	}
	if syncErr != nil || closeErr != nil {
		return 0, &errPermanent{"cannot write to the SD card"}
	}
	size := offset + written
	if size > fatFileLimit {
		_ = os.Remove(part)
		return 0, &errPermanent{"a file is larger than the SD card's 4 GB file limit"}
	}
	if o.format == "" && s.Size > 0 && size != s.Size {
		_ = os.Remove(part)
		return 0, errors.New("downloaded size does not match the server")
	}
	if err := os.Rename(part, dest); err != nil {
		return 0, &errPermanent{"cannot write to the SD card"}
	}
	return size, nil
}

type progressReader struct {
	r   io.Reader
	add func(int64)
}

func (p *progressReader) Read(b []byte) (int, error) {
	n, err := p.r.Read(b)
	if n > 0 {
		p.add(int64(n))
	}
	return n, err
}

// saveCover writes cover.jpg once per album folder; failures are not fatal.
func (o *offline) saveCover(ctx context.Context, coverID, relDir string) {
	rel := relDir + "/cover.jpg"
	if coverID == "" || !validID(coverID) || !safeRel(rel) {
		return
	}
	if _, err := os.Lstat(o.abs(rel)); err == nil {
		return
	}
	u, err := o.svc.upstreamURL("getCoverArt", url.Values{"id": {coverID}, "size": {"600"}})
	if err != nil {
		return
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return
	}
	res, err := o.svc.api.Do(req)
	if err != nil {
		return
	}
	defer res.Body.Close()
	// Only real JPEG artwork becomes cover.jpg; server placeholders are often PNG/WebP.
	if res.StatusCode != http.StatusOK || !strings.HasPrefix(strings.ToLower(res.Header.Get("Content-Type")), "image/jpeg") {
		return
	}
	data, err := io.ReadAll(io.LimitReader(res.Body, 4<<20+1))
	if err != nil || len(data) > 4<<20 {
		return
	}
	if writeAtomic(o.abs(rel), data) == nil {
		o.mu.Lock()
		o.man.Covers[rel] = true
		_ = o.saveManifest()
		o.mu.Unlock()
	}
}

// writePlaylist stores an .m3u8 with paths relative to <root>/Playlists.
func (o *offline) writePlaylist(id, name string, paths []string) {
	var b strings.Builder
	b.WriteString("#EXTM3U\n")
	for _, p := range paths {
		b.WriteString("../" + p + "\n")
	}
	rel := "Playlists/" + fatName(name) + ".m3u8"
	o.mu.Lock()
	defer o.mu.Unlock()
	if old := o.man.Playlists[id]; old != "" && old != rel {
		_ = os.Remove(o.abs(old))
	}
	for other, p := range o.man.Playlists {
		if other != id && strings.EqualFold(p, rel) {
			rel = "Playlists/" + fatName(name+" "+id) + ".m3u8"
		}
	}
	if os.MkdirAll(filepath.Dir(o.abs(rel)), 0o755) == nil && writeAtomic(o.abs(rel), []byte(b.String())) == nil {
		o.man.Playlists[id] = rel
		_ = o.saveManifest()
	}
}

// remove deletes this feature's files for an album (all its songs) or a playlist (its ownership).
func (o *offline) remove(kind, id string) int {
	key := kind + ":" + id
	o.cancelJob(key)
	o.mu.Lock()
	defer o.mu.Unlock()
	removed := 0
	dirs := map[string]bool{}
	for songID, t := range o.man.Tracks {
		owners := t.Owners[:0:0]
		for _, owner := range t.Owners {
			if owner != key {
				owners = append(owners, owner)
			}
		}
		if kind == "album" && t.AlbumID == id {
			owners = nil
		}
		if len(owners) == len(t.Owners) {
			continue
		}
		t.Owners = owners
		if len(owners) == 0 {
			if err := os.Remove(o.abs(t.Path)); err == nil || errors.Is(err, os.ErrNotExist) {
				removed++
			}
			dirs[filepath.ToSlash(filepath.Dir(filepath.FromSlash(t.Path)))] = true
			delete(o.man.Tracks, songID)
		}
	}
	if kind == "playlist" {
		if p := o.man.Playlists[id]; p != "" {
			_ = os.Remove(o.abs(p))
			delete(o.man.Playlists, id)
			dirs["Playlists"] = true
		}
	}
	keys := make([]string, 0, len(dirs))
	for d := range dirs {
		keys = append(keys, d)
	}
	sort.Strings(keys)
	for _, d := range keys {
		o.pruneDir(d)
	}
	for _, j := range o.jobs {
		if j.ID == key && j.State != "queued" && j.State != "active" {
			j.State, j.Done, j.Bytes, j.Error = "removed", 0, 0, ""
		}
	}
	_ = o.saveManifest()
	return removed
}

// pruneDir removes our cover and then empty folders up to, but not including, the root.
func (o *offline) pruneDir(rel string) {
	for rel != "." && rel != "" && safeRel(rel) {
		dir := o.abs(rel)
		entries, err := os.ReadDir(dir)
		if err != nil {
			return
		}
		kept := entries[:0:0]
		for _, e := range entries {
			// Interrupted downloads use ".<name>.part"; nothing else is removed here.
			if name := e.Name(); strings.HasPrefix(name, ".") && strings.HasSuffix(name, ".part") && e.Type().IsRegular() {
				_ = os.Remove(filepath.Join(dir, name))
				continue
			}
			kept = append(kept, e)
		}
		entries = kept
		cover := rel + "/cover.jpg"
		if len(entries) == 1 && entries[0].Name() == "cover.jpg" && o.man.Covers[cover] {
			if os.Remove(o.abs(cover)) == nil {
				delete(o.man.Covers, cover)
				entries = nil
			}
		}
		if len(entries) != 0 || os.Remove(dir) != nil {
			return
		}
		rel = filepath.ToSlash(filepath.Dir(filepath.FromSlash(rel)))
	}
}

// summary reports how many of the given songs are on the card and the collection's job state.
func (o *offline) summary(kind, id string, songs []wireTrack) (OfflineSummary, map[string]bool) {
	o.mu.Lock()
	defer o.mu.Unlock()
	have := map[string]bool{}
	sum := OfflineSummary{Total: len(songs)}
	for _, s := range songs {
		if o.man.Tracks[s.ID] != nil {
			have[s.ID] = true
			sum.Downloaded++
		}
	}
	for _, j := range o.jobs {
		if j.ID == kind+":"+id {
			sum.State, sum.Done, sum.Error = j.State, j.Done, j.Error
			if sum.State == "removed" {
				sum.State = ""
			}
		}
	}
	return sum, have
}

// ---- HTTP. ----

func decodeJSON(w http.ResponseWriter, r *http.Request, v any) error {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", "POST")
		return &apiError{405, "method_not_allowed", "POST required"}
	}
	if !strings.HasPrefix(strings.ToLower(r.Header.Get("Content-Type")), "application/json") {
		return bad("application/json required")
	}
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096))
	d.DisallowUnknownFields()
	if d.Decode(v) != nil || d.Decode(&struct{}{}) != io.EOF {
		return bad("one valid JSON object required")
	}
	return nil
}

type collectionRequest struct {
	Kind string `json:"kind"`
	ID   string `json:"id"`
}

func (c collectionRequest) valid() bool {
	return (c.Kind == "album" || c.Kind == "playlist") && validID(c.ID)
}

func (s *service) downloads(w http.ResponseWriter, r *http.Request) {
	o := s.offline
	switch r.URL.Path {
	case "/v1/downloads":
		if r.Method == http.MethodGet {
			free, err := o.freeSpace(filepath.Dir(o.root))
			if err != nil {
				free = -1
			}
			o.mu.Lock()
			count := len(o.man.Tracks)
			o.mu.Unlock()
			jsonResponse(w, 200, map[string]any{"jobs": o.snapshot(), "free_bytes": free, "reserve_bytes": o.reserve,
				"folder": filepath.Base(o.root), "downloaded_tracks": count})
			return
		}
		var c collectionRequest
		if err := decodeJSON(w, r, &c); err != nil {
			fail(w, err)
			return
		}
		if !c.valid() {
			fail(w, bad("kind must be album or playlist with a valid id"))
			return
		}
		j := o.enqueue(c.Kind, c.ID)
		o.mu.Lock()
		out := *j
		o.mu.Unlock()
		out.cancel = nil
		jsonResponse(w, 202, map[string]any{"job": out})
	case "/v1/downloads/cancel", "/v1/offline/remove":
		var c collectionRequest
		if err := decodeJSON(w, r, &c); err != nil {
			fail(w, err)
			return
		}
		if !c.valid() {
			fail(w, bad("kind must be album or playlist with a valid id"))
			return
		}
		if r.URL.Path == "/v1/downloads/cancel" {
			jsonResponse(w, 200, map[string]any{"cancelled": o.cancelJob(c.Kind + ":" + c.ID)})
		} else {
			jsonResponse(w, 200, map[string]any{"removed_files": o.remove(c.Kind, c.ID)})
		}
	default:
		http.NotFound(w, r)
	}
}
