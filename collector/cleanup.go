package main

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const retention = 30 * 24 * time.Hour

var errCleanupChanged = errors.New("FILE_CHANGED_OR_RECENT")

type CleanupFile struct {
	Path     string `json:"path"`
	Root     string `json:"root"`
	Size     int64  `json:"size"`
	Modified int64  `json:"modified_ns"`
	Recent   int64  `json:"recent_ns"`
	RecentAt string `json:"last_activity_at"`
	Identity string `json:"file_id"`
	State    string `json:"state,omitempty"`
	Error    string `json:"error,omitempty"`
}
type CleanupRoot struct {
	Path       string   `json:"path"`
	Candidates int      `json:"candidates"`
	Bytes      int64    `json:"bytes"`
	Errors     []string `json:"errors"`
}
type CleanupPlan struct {
	ID     string
	At     time.Time
	Cutoff time.Time
	Files  []CleanupFile
	Roots  []CleanupRoot
}
type CleanupReport struct {
	ID           string        `json:"id"`
	Mode         string        `json:"mode"`
	Cutoff       string        `json:"cutoff"`
	Roots        []CleanupRoot `json:"roots"`
	Count        int           `json:"count"`
	Bytes        int64         `json:"bytes"`
	Deleted      int           `json:"deleted"`
	DeletedBytes int64         `json:"deleted_bytes"`
	Skipped      int           `json:"skipped"`
	Failed       int           `json:"failed"`
	Samples      []CleanupFile `json:"samples"`
	Completed    string        `json:"completed_at,omitempty"`
	Log          string        `json:"log,omitempty"`
	Error        string        `json:"error,omitempty"`
	Active       string        `json:"active,omitempty"`
}

func cleanupKey(path string) string { return strings.ToLower(filepath.Clean(path)) }
func withinCleanup(root, path string) bool {
	rel, e := filepath.Rel(root, path)
	return e == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel)
}
func protectedCleanup(path string) bool {
	for _, part := range strings.FieldsFunc(filepath.Clean(path), func(c rune) bool { return c == '/' || c == '\\' }) {
		switch strings.ToLower(part) {
		case ".git", ".ssh", ".aws", ".codex", ".agents", "windows", "node_modules", "backend", "backend-dist", "collection-runs", "report-reviews", "collector", "cache":
			return true
		}
	}
	return false
}
func validateCleanupRoot(path, store string) (string, error) {
	if !filepath.IsAbs(path) || protectedCleanup(path) {
		return "", fmt.Errorf("SOURCE_ROOT_NOT_ALLOWED")
	}
	path = filepath.Clean(path)
	volume := filepath.VolumeName(path)
	if (!strings.HasPrefix(volume, `\\`) && path == volume+string(filepath.Separator)) || path == string(filepath.Separator) {
		return "", fmt.Errorf("DISK_ROOT_NOT_ALLOWED")
	}
	if strings.HasPrefix(volume, `\\`) && strings.EqualFold(strings.TrimRight(path, `\`), volume) {
		share := filepath.Base(volume)
		if (len(share) == 2 && share[1] == '$') || strings.EqualFold(share, "ADMIN$") {
			return "", fmt.Errorf("ADMIN_SHARE_ROOT_NOT_ALLOWED")
		}
	}
	if store != "" && (withinCleanup(path, store) || withinCleanup(store, path)) {
		return "", fmt.Errorf("CACHE_ROOT_NOT_ALLOWED")
	}
	canonical, e := filepath.EvalSymlinks(path)
	if e != nil {
		return "", e
	}
	if cleanupKey(path) != cleanupKey(canonical) {
		return "", fmt.Errorf("LINK_ROOT_NOT_ALLOWED")
	}
	info, e := os.Lstat(path)
	if e != nil {
		return "", e
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return "", fmt.Errorf("NOT_A_DIRECTORY")
	}
	return canonical, nil
}
func (s *Server) makeCleanupPlan(ctx context.Context, roots []string, now time.Time, progress func(CleanupReport)) *CleanupPlan {
	plan := &CleanupPlan{ID: digest(fmt.Sprint(now.UnixNano(), roots))[:24], At: now, Cutoff: now.Add(-retention)}
	seen := map[string]bool{}
	for _, root := range roots {
		if ctx.Err() != nil {
			break
		}
		row := CleanupRoot{Path: root, Errors: []string{}}
		canonical, e := validateCleanupRoot(root, s.store)
		if e != nil {
			row.Errors = append(row.Errors, e.Error())
			plan.Roots = append(plan.Roots, row)
			progress(plan.report("preview"))
			continue
		}
		row.Path = canonical
		initial := plan.report("preview")
		initial.Active = canonical
		progress(initial)
		e = filepath.WalkDir(canonical, func(path string, d fs.DirEntry, err error) error {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			if err != nil {
				row.Errors = append(row.Errors, path+": "+err.Error())
				return nil
			}
			if d.Type()&os.ModeSymlink != 0 {
				row.Errors = append(row.Errors, "LINK_SKIPPED: "+path)
				return nil
			}
			if d.IsDir() {
				if path != canonical && protectedCleanup(path) {
					return filepath.SkipDir
				}
				return nil
			}
			if !strings.EqualFold(filepath.Ext(path), ".txt") {
				return nil
			}
			st, e := d.Info()
			if e != nil {
				row.Errors = append(row.Errors, path+": "+e.Error())
				return nil
			}
			if !st.Mode().IsRegular() || !newestTime(st).Before(plan.Cutoff) {
				return nil
			}
			identity, current, e := cleanupIdentity(path)
			if e != nil {
				row.Errors = append(row.Errors, path+": "+e.Error())
				return nil
			}
			if current.Size() != st.Size() || !current.ModTime().Equal(st.ModTime()) || !newestTime(current).Equal(newestTime(st)) {
				row.Errors = append(row.Errors, "FILE_CHANGED_DURING_PREVIEW: "+path)
				return nil
			}
			st = current
			if !seen[cleanupKey(path)] {
				row.Candidates++
				row.Bytes += st.Size()
				seen[cleanupKey(path)] = true
				plan.Files = append(plan.Files, CleanupFile{Path: path, Root: canonical, Size: st.Size(), Modified: st.ModTime().UnixNano(), Recent: newestTime(st).UnixNano(), RecentAt: newestTime(st).UTC().Format(time.RFC3339Nano), Identity: identity})
			}
			if len(plan.Files)%100 == 0 {
				update := plan.report("preview")
				update.Active = canonical
				progress(update)
			}
			return nil
		})
		if e != nil {
			row.Errors = append(row.Errors, e.Error())
		}
		plan.Roots = append(plan.Roots, row)
		progress(plan.report("preview"))
	}
	return plan
}
func (p *CleanupPlan) report(mode string) CleanupReport {
	report := CleanupReport{ID: p.ID, Mode: mode, Cutoff: p.Cutoff.UTC().Format(time.RFC3339), Roots: append([]CleanupRoot{}, p.Roots...), Count: len(p.Files), Samples: []CleanupFile{}}
	for i, f := range p.Files {
		report.Bytes += f.Size
		if i < 50 {
			report.Samples = append(report.Samples, f)
		}
	}
	return report
}
func verifyCleanupFile(file CleanupFile, info os.FileInfo, cutoff time.Time) error {
	if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 || info.Size() != file.Size || info.ModTime().UnixNano() != file.Modified || newestTime(info).UnixNano() != file.Recent || !newestTime(info).Before(cutoff) {
		return errCleanupChanged
	}
	return nil
}
func (s *Server) deleteCleanupPlan(ctx context.Context, p *CleanupPlan, progress func(CleanupReport)) CleanupReport {
	report := p.report("delete")
	report.Samples = []CleanupFile{}
	if time.Since(p.At) > 10*time.Minute {
		report.Error = "PREVIEW_EXPIRED"
		return report
	}
	journalPath := filepath.Join(s.store, "maintenance", p.ID+"-deletion.jsonl")
	if e := os.MkdirAll(filepath.Dir(journalPath), 0700); e != nil {
		report.Error = "AUDIT_UNAVAILABLE: " + e.Error()
		return report
	}
	journal, e := os.OpenFile(journalPath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		report.Error = "AUDIT_UNAVAILABLE: " + e.Error()
		return report
	}
	defer journal.Close()
	writeAudit := func(event string, file CleanupFile) error {
		b, e := json.Marshal(map[string]any{"at": time.Now().UTC().Format(time.RFC3339Nano), "event": event, "file": file})
		if e != nil {
			return e
		}
		if _, e = journal.Write(append(b, '\n')); e != nil {
			return e
		}
		return journal.Sync()
	}
	for i := range p.Files {
		if ctx.Err() != nil {
			break
		}
		file := p.Files[i]
		report.Active = file.Path
		if e := writeAudit("attempt", file); e != nil {
			report.Error = "AUDIT_WRITE_FAILED: " + e.Error()
			break
		}
		canonical, e := filepath.EvalSymlinks(file.Path)
		if e != nil || cleanupKey(canonical) != cleanupKey(file.Path) || !withinCleanup(file.Root, canonical) {
			file.State = "skipped"
			file.Error = "PATH_CHANGED"
			report.Skipped++
		} else if e = removeVerified(ctx, file, p.Cutoff); e != nil {
			file.Error = e.Error()
			if errors.Is(e, errCleanupChanged) {
				file.State = "skipped"
				report.Skipped++
			} else {
				file.State = "failed"
				report.Failed++
			}
		} else {
			file.State = "deleted"
			report.Deleted++
			report.DeletedBytes += file.Size
		}
		p.Files[i] = file
		if len(report.Samples) < 50 {
			report.Samples = append(report.Samples, file)
		}
		if e := writeAudit("result", file); e != nil {
			report.Error = "AUDIT_WRITE_FAILED: " + e.Error()
			break
		}
		if i%20 == 0 {
			progress(report)
		}
	}
	return report
}
func (s *Server) serveMaintenance(w http.ResponseWriter, r *http.Request) {
	if len(s.cleanupToken) < 32 || subtle.ConstantTimeCompare([]byte(r.Header.Get("X-DMT-Maintenance")), []byte(s.cleanupToken)) != 1 {
		http.Error(w, "A PC administrator only", 403)
		return
	}
	if r.Method != "POST" {
		http.Error(w, "POST required", 405)
		return
	}
	var req struct {
		Roots []string `json:"roots"`
		Plan  string   `json:"plan"`
	}
	if json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&req) != nil {
		http.Error(w, "Invalid request", 400)
		return
	}
	mode := strings.TrimPrefix(r.URL.Path, "/maintenance/")
	if mode != "preview" && mode != "delete" {
		http.Error(w, "Not found", 404)
		return
	}
	if mode == "preview" && (len(req.Roots) == 0 || len(req.Roots) > 100) {
		http.Error(w, "Invalid roots", 400)
		return
	}
	s.mu.Lock()
	if s.maintenance {
		s.mu.Unlock()
		http.Error(w, "Maintenance busy", 409)
		return
	}
	for _, j := range s.jobs {
		j.mu.Lock()
		busy := j.State == "running"
		j.mu.Unlock()
		if busy {
			s.mu.Unlock()
			http.Error(w, "Collection busy", 409)
			return
		}
	}
	var plan *CleanupPlan
	if mode == "delete" {
		plan = s.plans[req.Plan]
		if plan == nil || time.Since(plan.At) > 10*time.Minute {
			s.mu.Unlock()
			http.Error(w, "Fresh preview required", 409)
			return
		}
		delete(s.plans, req.Plan)
	}
	s.maintenance = true
	if mode == "preview" {
		s.plans = nil
	}
	id := digest(fmt.Sprint(time.Now().UnixNano(), mode))[:24]
	ctx, cancel := context.WithCancel(context.Background())
	job := &Job{ID: id, State: "running", Started: time.Now().UTC().Format(time.RFC3339Nano), seen: time.Now(), cancel: cancel}
	s.jobs[id] = job
	s.mu.Unlock()
	go func() {
		publish := func(report CleanupReport) { job.mu.Lock(); job.Maintenance = &report; job.mu.Unlock() }
		if mode == "preview" {
			plan = s.makeCleanupPlan(ctx, req.Roots, time.Now(), publish)
		}
		report := plan.report(mode)
		if mode == "delete" {
			report = s.deleteCleanupPlan(ctx, plan, publish)
		}
		report.Completed = time.Now().UTC().Format(time.RFC3339Nano)
		if ctx.Err() != nil {
			report.Error = "CANCELLED"
		}
		report.Log = filepath.Join(s.store, "maintenance", id+".json")
		if e := atomicJSON(report.Log, map[string]any{"report": report, "files": plan.Files}); e != nil {
			report.Error = "AUDIT_WRITE_FAILED: " + e.Error()
		}
		if mode == "preview" && ctx.Err() == nil && report.Error == "" {
			s.mu.Lock()
			s.plans = map[string]*CleanupPlan{plan.ID: plan}
			s.mu.Unlock()
		}
		s.mu.Lock()
		job.mu.Lock()
		job.Maintenance = &report
		job.State = "complete"
		if ctx.Err() != nil {
			job.State = "cancelled"
		}
		s.maintenance = false
		job.mu.Unlock()
		s.mu.Unlock()
	}()
	writeJSON(w, map[string]string{"id": id})
}
