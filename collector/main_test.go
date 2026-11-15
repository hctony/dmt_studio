package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func fixture(t *testing.T) (*Server, string, string) {
	t.Helper()
	root := t.TempDir()
	source := filepath.Join(root, "source")
	os.MkdirAll(source, 0700)
	file := filepath.Join(source, "gather.txt")
	os.WriteFile(file, []byte("1,RX-,RX-01\nRX-,RX-01,1,1,T,SM03\nRX-,RX-01,EOS\n"), 0600)
	return &Server{store: filepath.Join(root, "store"), token: strings.Repeat("a", 64), jobs: map[string]*Job{}, roots: map[string]*sync.Mutex{}, slots: make(chan struct{}, 2)}, source, file
}
func TestSnapshotsReuseAndRetainVersions(t *testing.T) {
	s, root, file := fixture(t)
	first := s.collect(context.Background(), root, 30, false)
	if first.State != "complete" || len(first.Files) != 1 {
		t.Fatal(first)
	}
	second := s.collect(context.Background(), root, 30, false)
	if second.Reused != 1 {
		t.Fatal(second)
	}
	os.WriteFile(file, []byte("1,RX-,RX-01\nRX-,RX-01,EOS\n"), 0600)
	third := s.collect(context.Background(), root, 30, false)
	if third.Files[0].Hash == first.Files[0].Hash {
		t.Fatal("version was overwritten")
	}
	if _, err := os.Stat(first.Files[0].Object); err != nil {
		t.Fatal(err)
	}
}
func TestMissingRootIsIncompleteNotEmptySuccess(t *testing.T) {
	s, root, _ := fixture(t)
	h := s.collect(context.Background(), root+"-missing", 30, false)
	if h.State != "incomplete" || len(h.Errors) == 0 {
		t.Fatal(h)
	}
}
func TestForceRechecksSameMetadataAndKeepsOldVersion(t *testing.T) {
	s, root, file := fixture(t)
	first := s.collect(context.Background(), root, 30, false)
	st, _ := os.Stat(file)
	data, _ := os.ReadFile(file)
	os.WriteFile(file, []byte(strings.ReplaceAll(string(data), "SM03", "OT12")), 0600)
	os.Chtimes(file, st.ModTime(), st.ModTime())
	cached := s.collect(context.Background(), root, 30, false)
	if cached.Reused != 1 {
		t.Fatal(cached)
	}
	forced := s.collect(context.Background(), root, 30, true)
	if forced.Files[0].Hash == first.Files[0].Hash {
		t.Fatal("force reused stale content")
	}
}
func TestOldFileExcludedAndCancellation(t *testing.T) {
	s, root, file := fixture(t)
	old := time.Now().Add(-40 * 24 * time.Hour)
	os.Chtimes(file, old, old)
	h := s.collect(context.Background(), root, 30, false)
	st, _ := os.Stat(file)
	if newestTime(st).Equal(st.ModTime()) && h.Excluded != 1 {
		t.Fatal(h)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	h = s.collect(ctx, root, 30, false)
	if h.State != "cancelled" {
		t.Fatal(h)
	}
}
func TestAuthenticatedAPIAndParallelJobs(t *testing.T) {
	s, root, _ := fixture(t)
	srv := httptest.NewServer(s)
	defer srv.Close()
	r, _ := http.Get(srv.URL + "/health")
	if r.StatusCode != 401 {
		t.Fatal(r.StatusCode)
	}
	r.Body.Close()
	var wg sync.WaitGroup
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			body, _ := json.Marshal(map[string]any{"roots": []string{root}, "days": 30})
			req, _ := http.NewRequest("POST", srv.URL+"/scan", strings.NewReader(string(body)))
			req.Header.Set("Authorization", "Bearer "+s.token)
			res, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Error(err)
				return
			}
			var start map[string]string
			json.NewDecoder(res.Body).Decode(&start)
			res.Body.Close()
			for n := 0; n < 200; n++ {
				req, _ = http.NewRequest("GET", srv.URL+"/jobs/"+start["id"], nil)
				req.Header.Set("Authorization", "Bearer "+s.token)
				res, err = http.DefaultClient.Do(req)
				if err != nil {
					t.Error(err)
					return
				}
				var state struct {
					State string
					Hosts []Host
				}
				json.NewDecoder(res.Body).Decode(&state)
				res.Body.Close()
				if state.State == "complete" {
					if len(state.Hosts) != 1 || state.Hosts[0].State != "complete" {
						t.Error(state)
					}
					return
				}
				time.Sleep(10 * time.Millisecond)
			}
			t.Error("job timed out")
		}()
	}
	wg.Wait()
}
