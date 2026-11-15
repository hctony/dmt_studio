package main

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type File struct {
	Path     string `json:"path"`
	Object   string `json:"object"`
	Hash     string `json:"sha256"`
	Size     int64  `json:"size"`
	Mtime    int64  `json:"mtime_ns"`
	Verified int64  `json:"verified"`
}
type Host struct {
	Path      string   `json:"path"`
	State     string   `json:"state"`
	Files     []File   `json:"files"`
	Errors    []string `json:"errors"`
	Found     int      `json:"found"`
	Excluded  int      `json:"excluded"`
	Reused    int      `json:"reused"`
	Completed string   `json:"completed_at"`
}
type Job struct {
	ID       string            `json:"id"`
	State    string            `json:"state"`
	Hosts    []Host            `json:"hosts"`
	Started  string            `json:"started_at"`
	Active   []string          `json:"active"`
	Progress map[string][2]int `json:"progress"`
	mu       sync.Mutex
	seen     time.Time
	cancel   context.CancelFunc
}
type Server struct {
	store, token string
	mu           sync.Mutex
	jobs         map[string]*Job
	roots        map[string]*sync.Mutex
	slots        chan struct{}
}

func digest(s string) string { h := sha256.Sum256([]byte(s)); return hex.EncodeToString(h[:]) }
func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+s.token)) != 1 {
		http.Error(w, "Unauthorized", 401)
		return
	}
	if r.URL.Path == "/health" {
		s.mu.Lock()
		active := 0
		for _, j := range s.jobs {
			j.mu.Lock()
			if j.State == "running" {
				active++
			}
			j.mu.Unlock()
		}
		s.mu.Unlock()
		writeJSON(w, map[string]any{"ok": true, "active": active})
		return
	}
	if r.URL.Path == "/scan" && r.Method == "POST" {
		var req struct {
			Roots []string `json:"roots"`
			Days  int      `json:"days"`
			Force bool     `json:"force"`
		}
		if json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&req) != nil || len(req.Roots) == 0 || len(req.Roots) > 100 {
			http.Error(w, "Invalid roots", 400)
			return
		}
		if req.Days < 1 || req.Days > 30 {
			req.Days = 30
		}
		ctx, cancel := context.WithCancel(context.Background())
		id := digest(fmt.Sprint(time.Now().UnixNano(), req.Roots))[:24]
		j := &Job{ID: id, State: "running", Started: time.Now().UTC().Format(time.RFC3339Nano), seen: time.Now(), cancel: cancel}
		s.mu.Lock()
		if len(s.jobs) >= 100 {
			for k, old := range s.jobs {
				old.mu.Lock()
				done := old.State != "running"
				old.mu.Unlock()
				if done {
					delete(s.jobs, k)
				}
			}
		}
		s.jobs[id] = j
		s.mu.Unlock()
		go s.run(ctx, j, req.Roots, req.Days, req.Force)
		writeJSON(w, map[string]string{"id": id})
		return
	}
	id := strings.TrimPrefix(r.URL.Path, "/jobs/")
	s.mu.Lock()
	j := s.jobs[id]
	s.mu.Unlock()
	if j == nil {
		http.Error(w, "Unknown job", 404)
		return
	}
	if r.Method == "DELETE" {
		j.cancel()
		j.mu.Lock()
		done := j.State != "running"
		j.mu.Unlock()
		if done {
			s.mu.Lock()
			delete(s.jobs, id)
			s.mu.Unlock()
			writeJSON(w, map[string]string{"state": "released"})
			return
		}
	}
	j.mu.Lock()
	defer j.mu.Unlock()
	j.seen = time.Now()
	if r.URL.Query().Get("summary") == "1" && j.State == "running" {
		hosts := []map[string]any{}
		for _, host := range j.Hosts {
			hosts = append(hosts, map[string]any{"path": host.Path, "state": host.State, "found": host.Found})
		}
		writeJSON(w, map[string]any{"id": j.ID, "state": j.State, "hosts": hosts, "active": j.Active, "progress": j.Progress})
		return
	}
	writeJSON(w, j)
}
func (s *Server) run(ctx context.Context, j *Job, roots []string, days int, force bool) {
	var wg sync.WaitGroup
	for _, root := range roots {
		root := root
		wg.Add(1)
		go func() {
			defer wg.Done()
			select {
			case s.slots <- struct{}{}:
				defer func() { <-s.slots }()
			case <-ctx.Done():
				return
			}
			s.mu.Lock()
			lock := s.roots[root]
			if lock == nil {
				lock = &sync.Mutex{}
				s.roots[root] = lock
			}
			s.mu.Unlock()
			lock.Lock()
			defer lock.Unlock()
			j.mu.Lock()
			j.Active = append(j.Active, root)
			if j.Progress == nil {
				j.Progress = map[string][2]int{}
			}
			j.mu.Unlock()
			h := s.collect(ctx, root, days, force, func(found, copied int) { j.mu.Lock(); j.Progress[root] = [2]int{found, copied}; j.mu.Unlock() })
			j.mu.Lock()
			for i, p := range j.Active {
				if p == root {
					j.Active = append(j.Active[:i], j.Active[i+1:]...)
					break
				}
			}
			j.Hosts = append(j.Hosts, h)
			j.mu.Unlock()
		}()
	}
	wg.Wait()
	j.mu.Lock()
	defer j.mu.Unlock()
	j.State = "complete"
	if ctx.Err() != nil {
		j.State = "cancelled"
	}
}
func (s *Server) collect(ctx context.Context, root string, days int, force bool, callbacks ...func(int, int)) Host {
	h := Host{Path: root, State: "complete", Files: []File{}, Errors: []string{}}
	if ctx.Err() != nil {
		h.State = "cancelled"
		return h
	}
	indexPath := filepath.Join(s.store, "indexes", digest(root)+".json")
	previous := map[string]File{}
	if data, err := os.ReadFile(indexPath); err == nil {
		json.Unmarshal(data, &previous)
	}
	next := map[string]File{}
	cutoff := time.Now().Add(-time.Duration(days) * 24 * time.Hour)
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if err != nil {
			h.Errors = append(h.Errors, path+": "+err.Error())
			return nil
		}
		if d.Type()&os.ModeSymlink != 0 {
			h.Errors = append(h.Errors, "LINK_NOT_SCANNED: "+path)
			return nil
		}
		if d.IsDir() || !strings.EqualFold(filepath.Ext(path), ".txt") {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			h.Errors = append(h.Errors, path+": "+err.Error())
			return nil
		}
		if newestTime(info).Before(cutoff) {
			h.Excluded++
			return nil
		}
		h.Found++
		defer func() {
			if len(callbacks) > 0 {
				callbacks[0](h.Found, len(h.Files))
			}
		}()
		old, ok := previous[path]
		if !force && ok && old.Size == info.Size() && old.Mtime == info.ModTime().UnixNano() && time.Now().Unix()-old.Verified < 86400 {
			if st, e := os.Stat(old.Object); e == nil && st.Size() == old.Size {
				next[path] = old
				h.Files = append(h.Files, old)
				h.Reused++
				return nil
			}
		}
		var f File
		for attempt := 0; attempt < 2; attempt++ {
			f, err = s.snapshot(ctx, path)
			if err == nil || ctx.Err() != nil {
				break
			}
		}
		if err != nil {
			h.Errors = append(h.Errors, path+": "+err.Error())
			return nil
		}
		next[path] = f
		h.Files = append(h.Files, f)
		return nil
	})
	if err != nil {
		h.Errors = append(h.Errors, err.Error())
	}
	if len(h.Errors) > 0 {
		h.State = "incomplete"
	}
	if ctx.Err() != nil {
		h.State = "cancelled"
	}
	if len(h.Errors) > 0 {
		for path, file := range previous {
			if _, ok := next[path]; !ok {
				next[path] = file
			}
		}
	}
	if ctx.Err() == nil {
		if err := atomicJSON(indexPath, next); err != nil {
			h.Errors = append(h.Errors, err.Error())
			h.State = "incomplete"
		}
	}
	h.Completed = time.Now().UTC().Format(time.RFC3339Nano)
	return h
}
func (s *Server) snapshot(ctx context.Context, path string) (File, error) {
	var result File
	source, err := os.Open(path)
	if err != nil {
		return result, err
	}
	defer source.Close()
	before, err := source.Stat()
	if err != nil {
		return result, err
	}
	dir := filepath.Join(s.store, "objects")
	if err = os.MkdirAll(dir, 0700); err != nil {
		return result, err
	}
	temp, err := os.CreateTemp(dir, ".pending-")
	if err != nil {
		return result, err
	}
	defer os.Remove(temp.Name())
	defer temp.Close()
	hash := sha256.New()
	buf := make([]byte, 128*1024)
	var size int64
	for {
		if err = ctx.Err(); err != nil {
			return result, err
		}
		n, e := source.Read(buf)
		if n > 0 {
			if _, err = temp.Write(buf[:n]); err != nil {
				return result, err
			}
			hash.Write(buf[:n])
			size += int64(n)
		}
		if e == io.EOF {
			break
		}
		if e != nil {
			return result, e
		}
	}
	after, err := os.Stat(path)
	if err != nil {
		return result, err
	}
	if before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) || size != after.Size() {
		return result, fmt.Errorf("file changed while reading")
	}
	if err = temp.Sync(); err != nil {
		return result, err
	}
	if err = temp.Close(); err != nil {
		return result, err
	}
	sum := hex.EncodeToString(hash.Sum(nil))
	object := filepath.Join(dir, sum+".txt")
	if _, e := os.Stat(object); e != nil {
		if err = os.Rename(temp.Name(), object); err != nil {
			if st, e := os.Stat(object); e != nil || st.Size() != size {
				return result, err
			}
		}
	}
	return File{Path: path, Object: object, Hash: sum, Size: size, Mtime: before.ModTime().UnixNano(), Verified: time.Now().Unix()}, nil
}
func atomicJSON(path string, v any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	b, err := json.Marshal(v)
	if err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".index-")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if _, err = f.Write(b); err != nil {
		f.Close()
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	return os.Rename(f.Name(), path)
}
func main() {
	if os.Getenv("DMT_COLLECTOR_PARENT_PIPE") == "1" {
		go func() { io.Copy(io.Discard, os.Stdin); os.Exit(0) }()
	}
	store := os.Getenv("DMT_COLLECTOR_STORE")
	token := os.Getenv("DMT_COLLECTOR_TOKEN")
	if store == "" || len(token) < 32 {
		os.Exit(2)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:"+os.Getenv("DMT_COLLECTOR_PORT"))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(3)
	}
	server := &Server{store: store, token: token, jobs: map[string]*Job{}, roots: map[string]*sync.Mutex{}, slots: make(chan struct{}, 2)}
	go func() {
		for range time.Tick(10 * time.Second) {
			server.mu.Lock()
			for id, job := range server.jobs {
				job.mu.Lock()
				if job.State == "running" && time.Since(job.seen) > time.Minute {
					job.cancel()
				}
				if job.State != "running" && time.Since(job.seen) > 5*time.Minute {
					delete(server.jobs, id)
				}
				job.mu.Unlock()
			}
			server.mu.Unlock()
		}
	}()
	json.NewEncoder(os.Stdout).Encode(map[string]string{"url": "http://" + listener.Addr().String()})
	http.Serve(listener, server)
}
