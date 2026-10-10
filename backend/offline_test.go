package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// fakeLibrary is a Subsonic server with two songs on one album and a playlist.
type fakeLibrary struct {
	mu          sync.Mutex
	audio       map[string][]byte
	ranges      []string
	ignoreRange bool
	failOnce    map[string]bool
	errorCode   int
	block       chan struct{}
	downloads   int
}

func newFakeLibrary() *fakeLibrary {
	return &fakeLibrary{audio: map[string][]byte{
		"s1": bytes.Repeat([]byte("FLACDATA"), 40000),
		"s2": bytes.Repeat([]byte("MP3DATA!"), 30000),
	}, failOnce: map[string]bool{}}
}

func (f *fakeLibrary) song(id, title string, track int, suffix string) string {
	return fmt.Sprintf(`{"id":%q,"title":%q,"artist":"Sơn Tùng","album":"Album: A/B?","albumId":"al1","track":%d,"discNumber":1,"year":2024,"suffix":%q,"size":%d,"coverArt":"al1","contentType":"audio/flac"}`,
		id, title, track, suffix, len(f.audio[id]))
}

func (f *fakeLibrary) handler(t *testing.T) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		authCheck(t, r)
		id := r.URL.Query().Get("id")
		switch path := r.URL.Path; {
		case strings.HasSuffix(path, "/getAlbum.view"):
			reply(w, `"album":{"id":"al1","name":"Album: A/B?","artist":"Sơn Tùng","year":2024,"song":[`+
				f.song("s1", "Tiếng \"1\"", 1, "flac")+`,`+f.song("s2", "../../escape", 2, "mp3")+`]}`)
		case strings.HasSuffix(path, "/getPlaylist.view"):
			reply(w, `"playlist":{"id":"pl1","name":"Road Trip","entry":[`+f.song("s2", "../../escape", 2, "mp3")+`]}`)
		case strings.HasSuffix(path, "/getCoverArt.view"):
			w.Header().Set("Content-Type", "image/jpeg")
			_, _ = w.Write([]byte("JPEG"))
		case strings.HasSuffix(path, "/download.view"):
			f.mu.Lock()
			f.downloads++
			f.ranges = append(f.ranges, r.Header.Get("Range"))
			fail, code, block, ignore := f.failOnce[id], f.errorCode, f.block, f.ignoreRange
			f.failOnce[id] = false
			data := f.audio[id]
			f.mu.Unlock()
			if code != 0 {
				reply(w, fmt.Sprintf(`"status":"failed","error":{"code":%d,"message":"secret detail"}`, code))
				return
			}
			if fail {
				hj, _ := w.(http.Hijacker)
				conn, _, _ := hj.Hijack()
				conn.Close()
				return
			}
			if block != nil {
				w.Header().Set("Content-Type", "audio/flac")
				w.Header().Set("Content-Length", fmt.Sprint(len(data)))
				w.WriteHeader(200)
				_, _ = w.Write(data[:1024])
				w.(http.Flusher).Flush()
				<-block
				return
			}
			w.Header().Set("Content-Type", "audio/flac")
			if ignore {
				r.Header.Del("Range")
			}
			http.ServeContent(w, r, "", time.Time{}, bytes.NewReader(data))
		default:
			http.NotFound(w, r)
		}
	}
}

func offlineService(t *testing.T, lib *fakeLibrary, root string) *service {
	t.Helper()
	up := httptest.NewServer(lib.handler(t))
	t.Cleanup(up.Close)
	s, err := newService(Config{ServerURL: up.URL + "/music/", Username: "alice", Password: "a secret with 日本語",
		AllowHTTP: true, OfflineDir: root, OfflineReserveMB: 1})
	if err != nil {
		t.Fatal(err)
	}
	s.offline.retryWait = func(int) time.Duration { return time.Millisecond }
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	go s.offline.run(ctx)
	return s
}

func post(s *service, endpoint, body string) *httptest.ResponseRecorder {
	return localRequest(s, http.MethodPost, endpoint, body)
}

func waitJob(t *testing.T, s *service, key string, states ...string) DownloadJob {
	t.Helper()
	end := time.Now().Add(5 * time.Second)
	for time.Now().Before(end) {
		for _, j := range s.offline.snapshot() {
			for _, st := range states {
				if j.ID == key && j.State == st {
					return j
				}
			}
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("job %s never reached %v: %+v", key, states, s.offline.snapshot())
	return DownloadJob{}
}

const (
	albumDir  = "Sơn Tùng/Album_ A_B_ (2024)"
	song1Path = albumDir + "/01 Tiếng _1_.flac"
	song2Path = albumDir + "/02 .._.._escape.mp3"
)

func TestOfflineAlbumDownloadAndStatus(t *testing.T) {
	lib, root := newFakeLibrary(), t.TempDir()
	s := offlineService(t, lib, root)
	if w := post(s, "/v1/downloads", `{"kind":"album","id":"al1"}`); w.Code != 202 {
		t.Fatalf("enqueue: %d %s", w.Code, w.Body)
	}
	job := waitJob(t, s, "album:al1", "done")
	if job.Name != "Album: A/B?" || job.Total != 2 || job.Done != 2 || job.Bytes != int64(len(lib.audio["s1"])+len(lib.audio["s2"])) {
		t.Fatalf("unexpected job %+v", job)
	}
	for rel, want := range map[string][]byte{song1Path: lib.audio["s1"], song2Path: lib.audio["s2"], albumDir + "/cover.jpg": []byte("JPEG")} {
		got, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
		if err != nil || !bytes.Equal(got, want) {
			t.Fatalf("%s: err=%v equal=%v", rel, err, bytes.Equal(got, want))
		}
	}
	entries, _ := os.ReadDir(filepath.Join(root, filepath.FromSlash(albumDir)))
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".part") {
			t.Fatalf("partial file left behind: %s", e.Name())
		}
	}
	body := localRequest(s, http.MethodGet, "/v1/album?id=al1", "").Body.String()
	if !strings.Contains(body, `"offline":{"downloaded":2,"total":2,"state":"done"`) || strings.Count(body, `"downloaded":true`) != 2 {
		t.Fatalf("album offline status missing: %s", body)
	}
	var list struct {
		Jobs             []DownloadJob `json:"jobs"`
		Folder           string        `json:"folder"`
		DownloadedTracks int           `json:"downloaded_tracks"`
	}
	if json.Unmarshal(localRequest(s, http.MethodGet, "/v1/downloads", "").Body.Bytes(), &list) != nil ||
		len(list.Jobs) != 1 || list.DownloadedTracks != 2 || list.Folder != filepath.Base(root) {
		t.Fatalf("download list: %+v", list)
	}
	// A second request re-checks files and downloads nothing again.
	before := lib.downloads
	post(s, "/v1/downloads", `{"kind":"album","id":"al1"}`)
	waitJob(t, s, "album:al1", "done")
	if lib.downloads != before {
		t.Fatalf("complete files were downloaded again")
	}
}

func TestOfflineResumeAndRestart(t *testing.T) {
	for _, ignore := range []bool{false, true} {
		lib, root := newFakeLibrary(), t.TempDir()
		lib.ignoreRange = ignore
		dir := filepath.Join(root, filepath.FromSlash(albumDir))
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		half := lib.audio["s1"][:100000]
		if err := os.WriteFile(filepath.Join(dir, ".01 Tiếng _1_.flac.part"), half, 0o644); err != nil {
			t.Fatal(err)
		}
		s := offlineService(t, lib, root)
		post(s, "/v1/downloads", `{"kind":"album","id":"al1"}`)
		waitJob(t, s, "album:al1", "done")
		got, _ := os.ReadFile(filepath.Join(root, filepath.FromSlash(song1Path)))
		if !bytes.Equal(got, lib.audio["s1"]) || lib.ranges[0] != "bytes=100000-" {
			t.Fatalf("ignoreRange=%v: resume failed, range %q", ignore, lib.ranges[0])
		}
	}
}

func TestOfflineFailuresAreReported(t *testing.T) {
	cases := []struct {
		name  string
		setup func(*fakeLibrary, *service)
		want  string
	}{
		{"downloads disabled", func(l *fakeLibrary, _ *service) { l.errorCode = 50 }, "does not allow downloads"},
		{"no space", func(_ *fakeLibrary, s *service) {
			s.offline.freeSpace = func(string) (int64, error) { return 1 << 20, nil }
		}, "not enough space"},
	}
	for _, c := range cases {
		lib, root := newFakeLibrary(), t.TempDir()
		s := offlineService(t, lib, root)
		c.setup(lib, s)
		post(s, "/v1/downloads", `{"kind":"album","id":"al1"}`)
		job := waitJob(t, s, "album:al1", "failed")
		if !strings.Contains(job.Error, c.want) || strings.Contains(job.Error, "secret") {
			t.Fatalf("%s: error %q", c.name, job.Error)
		}
		if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(song1Path))); err == nil {
			t.Fatalf("%s: file written despite failure", c.name)
		}
	}
}

func TestOfflineRetriesTransientFailure(t *testing.T) {
	lib, root := newFakeLibrary(), t.TempDir()
	lib.failOnce["s1"] = true
	s := offlineService(t, lib, root)
	post(s, "/v1/downloads", `{"kind":"album","id":"al1"}`)
	waitJob(t, s, "album:al1", "done")
	if got, _ := os.ReadFile(filepath.Join(root, filepath.FromSlash(song1Path))); !bytes.Equal(got, lib.audio["s1"]) {
		t.Fatal("retry did not complete the file")
	}
}

func TestOfflineNeverOverwritesUserFiles(t *testing.T) {
	lib, root := newFakeLibrary(), t.TempDir()
	dir := filepath.Join(root, filepath.FromSlash(albumDir))
	_ = os.MkdirAll(dir, 0o755)
	mine := filepath.Join(root, filepath.FromSlash(song1Path))
	if err := os.WriteFile(mine, []byte("my own file"), 0o644); err != nil {
		t.Fatal(err)
	}
	s := offlineService(t, lib, root)
	post(s, "/v1/downloads", `{"kind":"album","id":"al1"}`)
	waitJob(t, s, "album:al1", "done")
	if got, _ := os.ReadFile(mine); string(got) != "my own file" {
		t.Fatal("user file overwritten")
	}
	alt := filepath.Join(dir, "01 Tiếng _1_ (2).flac")
	if got, _ := os.ReadFile(alt); !bytes.Equal(got, lib.audio["s1"]) {
		t.Fatal("download not written beside the user's file")
	}
	post(s, "/v1/offline/remove", `{"kind":"album","id":"al1"}`)
	if _, err := os.Stat(mine); err != nil {
		t.Fatal("remove deleted a file it did not create")
	}
	if _, err := os.Stat(alt); err == nil {
		t.Fatal("remove kept a downloaded file")
	}
}

func TestOfflinePlaylistOwnershipAndRemoval(t *testing.T) {
	lib, root := newFakeLibrary(), t.TempDir()
	s := offlineService(t, lib, root)
	post(s, "/v1/downloads", `{"kind":"playlist","id":"pl1"}`)
	waitJob(t, s, "playlist:pl1", "done")
	m3u, err := os.ReadFile(filepath.Join(root, "Playlists", "Road Trip.m3u8"))
	if err != nil || string(m3u) != "#EXTM3U\n../"+song2Path+"\n" {
		t.Fatalf("playlist file: %q %v", m3u, err)
	}
	post(s, "/v1/downloads", `{"kind":"album","id":"al1"}`)
	waitJob(t, s, "album:al1", "done")
	// Removing the playlist keeps the song still owned by the downloaded album.
	if w := post(s, "/v1/offline/remove", `{"kind":"playlist","id":"pl1"}`); !strings.Contains(w.Body.String(), `"removed_files":0`) {
		t.Fatalf("playlist removal: %s", w.Body)
	}
	if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(song2Path))); err != nil {
		t.Fatal("album-owned song removed with playlist")
	}
	if _, err := os.Stat(filepath.Join(root, "Playlists")); err == nil {
		t.Fatal("empty Playlists folder kept")
	}
	if w := post(s, "/v1/offline/remove", `{"kind":"album","id":"al1"}`); !strings.Contains(w.Body.String(), `"removed_files":2`) {
		t.Fatalf("album removal: %s", w.Body)
	}
	entries, _ := os.ReadDir(root)
	if len(entries) != 1 || entries[0].Name() != manifestName {
		t.Fatalf("folders not pruned: %v", entries)
	}
	if body := localRequest(s, http.MethodGet, "/v1/album?id=al1", "").Body.String(); !strings.Contains(body, `"offline":{"downloaded":0,"total":2,"state":""`) {
		t.Fatalf("album still reported downloaded: %s", body)
	}
}

func TestOfflineCancelLeavesNoRecordedFile(t *testing.T) {
	lib, root := newFakeLibrary(), t.TempDir()
	lib.block = make(chan struct{})
	defer close(lib.block)
	s := offlineService(t, lib, root)
	post(s, "/v1/downloads", `{"kind":"album","id":"al1"}`)
	end := time.Now().Add(3 * time.Second)
	for time.Now().Before(end) {
		if j := s.offline.snapshot(); len(j) == 1 && j[0].Bytes > 0 {
			break
		}
		time.Sleep(5 * time.Millisecond)
	}
	if w := post(s, "/v1/downloads/cancel", `{"kind":"album","id":"al1"}`); !strings.Contains(w.Body.String(), `"cancelled":true`) {
		t.Fatalf("cancel: %s", w.Body)
	}
	waitJob(t, s, "album:al1", "cancelled")
	time.Sleep(50 * time.Millisecond)
	if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(song1Path))); err == nil {
		t.Fatal("cancelled download produced a final file")
	}
	s.offline.mu.Lock()
	recorded := len(s.offline.man.Tracks)
	s.offline.mu.Unlock()
	if recorded != 0 {
		t.Fatal("cancelled download recorded in manifest")
	}
}

func TestOfflineQueueSurvivesRestart(t *testing.T) {
	lib, root := newFakeLibrary(), t.TempDir()
	m := manifest{Version: 1, Queue: []string{"album:al1", "bogus", "album:../x\x00"}}
	data, _ := json.Marshal(m)
	_ = os.WriteFile(filepath.Join(root, manifestName), data, 0o644)
	s := offlineService(t, lib, root)
	waitJob(t, s, "album:al1", "done")
	if n := len(s.offline.snapshot()); n != 1 {
		t.Fatalf("invalid queue entries accepted: %d jobs", n)
	}
}

func TestOfflineCorruptManifestKept(t *testing.T) {
	root := t.TempDir()
	_ = os.WriteFile(filepath.Join(root, manifestName), []byte("{broken"), 0o644)
	s := offlineService(t, newFakeLibrary(), root)
	if len(s.offline.man.Tracks) != 0 {
		t.Fatal("corrupt manifest produced tracks")
	}
	if _, err := os.Stat(filepath.Join(root, manifestName+".corrupt")); err != nil {
		t.Fatal("corrupt manifest not preserved for inspection")
	}
}

func TestOfflineNamesAndValidation(t *testing.T) {
	for in, want := range map[string]string{
		`a/b\c:d*e?f"g<h>i|j`:   "a_b_c_d_e_f_g_h_i_j",
		"  trailing dots... ":   "trailing dots",
		"..":                    "Unknown",
		"":                      "Unknown",
		"Việt":                "Việt",
		strings.Repeat("ạ", 80): strings.Repeat("ạ", 33),
	} {
		if got := fatName(in); got != want {
			t.Errorf("fatName(%q) = %q, want %q", in, got, want)
		}
	}
	for _, p := range []string{"../x", "/abs", "a//b", `a\b`, "a/./b", ""} {
		if safeRel(p) {
			t.Errorf("safeRel accepted %q", p)
		}
	}
	tr := wireTrack{Title: "Song", Track: 3, DiscNumber: 2, Suffix: "FLAC", Artist: "Track Artist", Album: "LP"}
	if got := relPath(tr, "", "", 0, ""); got != "Track Artist/LP/2-03 Song.flac" {
		t.Errorf("relPath = %q", got)
	}
	if got := relPath(wireTrack{Title: "x", Suffix: "e/x"}, "A", "B", 1999, "opus"); got != "A/B (1999)/x.opus" {
		t.Errorf("transcoded relPath = %q", got)
	}
	s, _ := testService(t, func(http.ResponseWriter, *http.Request) {})
	for _, body := range []string{`{"kind":"song","id":"x"}`, `{"kind":"album","id":""}`, `{"kind":"album","id":"x","extra":1}`, `not json`} {
		if w := post(s, "/v1/downloads", body); w.Code != 400 {
			t.Errorf("POST %s accepted: %d", body, w.Code)
		}
	}
	if w := localRequest(s, http.MethodDelete, "/v1/offline/remove", ""); w.Code != 405 {
		t.Errorf("DELETE accepted: %d", w.Code)
	}
	if _, err := newService(Config{ServerURL: "https://x/", Username: "u", Password: "p", OfflineDir: "relative"}); err == nil {
		t.Error("relative offline_dir accepted")
	}
	if _, err := newService(Config{ServerURL: "https://x/", Username: "u", Password: "p", OfflineFormat: "../mp3"}); err == nil {
		t.Error("unsafe offline_format accepted")
	}
}

func TestOfflineFatLimitAndHostCheck(t *testing.T) {
	lib, root := newFakeLibrary(), t.TempDir()
	s := offlineService(t, lib, root)
	big := wireTrack{ID: "s1", Title: "Huge", Size: fatFileLimit + 1}
	job := &DownloadJob{ID: "album:al1", State: "active"}
	if _, err := s.offline.downloadSong(context.Background(), big, "A", "B", 0, "al1", job.ID, job); err == nil || !strings.Contains(err.Error(), "4 GB") {
		t.Fatalf("oversized file accepted: %v", err)
	}
	r := httptest.NewRequest(http.MethodGet, "http://evil.example/v1/downloads", nil)
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	if w.Code != 403 {
		t.Fatalf("foreign Host reached downloads: %d", w.Code)
	}
}
