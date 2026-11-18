package main

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

func oldCleanupFile(t *testing.T, path string, stamp time.Time) {
	t.Helper()
	if e := os.WriteFile(path, []byte("old test data"), 0600); e != nil {
		t.Fatal(e)
	}
	name, _ := syscall.UTF16PtrFromString(path)
	handle, e := syscall.CreateFile(name, 0x100, syscall.FILE_SHARE_READ|syscall.FILE_SHARE_WRITE|syscall.FILE_SHARE_DELETE, nil, syscall.OPEN_EXISTING, 0, 0)
	if e != nil {
		t.Fatal(e)
	}
	ft := syscall.NsecToFiletime(stamp.UnixNano())
	e = syscall.SetFileTime(handle, &ft, &ft, &ft)
	syscall.CloseHandle(handle)
	if e != nil {
		t.Fatal(e)
	}
}
func cleanupPlan(t *testing.T, s *Server, roots ...string) *CleanupPlan {
	t.Helper()
	return s.makeCleanupPlan(context.Background(), roots, time.Now(), func(CleanupReport) {})
}
func TestCleanupDeletesOnlyOldTxtWithVerifiedHandle(t *testing.T) {
	s, root, _ := fixture(t)
	old := filepath.Join(root, "past.TXT")
	oldCleanupFile(t, old, time.Now().Add(-31*24*time.Hour))
	for _, name := range []string{"past.xml", "past.rpt", "past.ski", "past.jpg"} {
		oldCleanupFile(t, filepath.Join(root, name), time.Now().Add(-40*24*time.Hour))
	}
	plan := cleanupPlan(t, s, root)
	if len(plan.Files) != 1 {
		t.Fatalf("candidates: %+v", plan.report("preview"))
	}
	report := s.deleteCleanupPlan(context.Background(), plan, func(CleanupReport) {})
	if report.Deleted != 1 || report.Failed != 0 {
		t.Fatalf("delete: %+v", report)
	}
	if _, e := os.Stat(old); !os.IsNotExist(e) {
		t.Fatalf("old TXT remains: %v", e)
	}
	for _, name := range []string{"gather.txt", "past.xml", "past.rpt", "past.ski", "past.jpg"} {
		if _, e := os.Stat(filepath.Join(root, name)); e != nil {
			t.Fatal(e)
		}
	}
	if _, e := os.Stat(filepath.Join(s.store, "maintenance", plan.ID+"-deletion.jsonl")); e != nil {
		t.Fatal(e)
	}
}
func TestCleanupPreservesRecentCreationOrModificationAndExactBoundary(t *testing.T) {
	s, root, _ := fixture(t)
	now := time.Now().Truncate(time.Second)
	created := filepath.Join(root, "recent-creation.txt")
	os.WriteFile(created, []byte("new"), 0600)
	os.Chtimes(created, now.Add(-40*24*time.Hour), now.Add(-40*24*time.Hour))
	modified := filepath.Join(root, "recent-modification.txt")
	oldCleanupFile(t, modified, now.Add(-40*24*time.Hour))
	os.Chtimes(modified, now, now)
	boundary := filepath.Join(root, "boundary.txt")
	oldCleanupFile(t, boundary, now.Add(-retention))
	plan := s.makeCleanupPlan(context.Background(), []string{root}, now, func(CleanupReport) {})
	if len(plan.Files) != 0 {
		t.Fatal(plan.Files)
	}
}
func TestCleanupSkipsChangedReplacedAndOpenFiles(t *testing.T) {
	s, root, _ := fixture(t)
	stamp := time.Now().Add(-40 * 24 * time.Hour)
	changed := filepath.Join(root, "changed.txt")
	oldCleanupFile(t, changed, stamp)
	replaced := filepath.Join(root, "replaced.txt")
	oldCleanupFile(t, replaced, stamp)
	locked := filepath.Join(root, "locked.txt")
	oldCleanupFile(t, locked, stamp)
	plan := cleanupPlan(t, s, root)
	os.WriteFile(changed, []byte("now being written"), 0600)
	if e := os.Rename(replaced, filepath.Join(root, "previous.dat")); e != nil {
		t.Fatal(e)
	}
	oldCleanupFile(t, replaced, stamp)
	writer, e := os.OpenFile(locked, os.O_WRONLY, 0600)
	if e != nil {
		t.Fatal(e)
	}
	defer writer.Close()
	report := s.deleteCleanupPlan(context.Background(), plan, func(CleanupReport) {})
	if report.Deleted != 0 || report.Failed != 1 || report.Skipped != 2 {
		t.Fatalf("unexpected deletion: %+v", report)
	}
	for _, name := range []string{changed, replaced, locked} {
		if _, e := os.Stat(name); e != nil {
			t.Fatal(e)
		}
	}
}
func TestCleanupProtectedAndMissingRootsDoNotBecomeSuccess(t *testing.T) {
	s, root, _ := fixture(t)
	plan := cleanupPlan(t, s, filepath.VolumeName(root)+`\`, s.store, filepath.Join(root, "offline"))
	if len(plan.Files) != 0 || len(plan.Roots) != 3 {
		t.Fatal(plan)
	}
	for _, row := range plan.Roots {
		if len(row.Errors) == 0 {
			t.Fatal(row)
		}
	}
}
func TestCleanupDeduplicatesRootsAndCancelOrExpiryDeletesNothing(t *testing.T) {
	s, root, _ := fixture(t)
	file := filepath.Join(root, "old.txt")
	oldCleanupFile(t, file, time.Now().Add(-40*24*time.Hour))
	plan := cleanupPlan(t, s, root, root)
	if len(plan.Files) != 1 {
		t.Fatal(plan.Files)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	report := s.deleteCleanupPlan(ctx, plan, func(CleanupReport) {})
	if report.Deleted != 0 {
		t.Fatal(report)
	}
	plan.At = time.Now().Add(-11 * time.Minute)
	report = s.deleteCleanupPlan(context.Background(), plan, func(CleanupReport) {})
	if report.Error != "PREVIEW_EXPIRED" {
		t.Fatal(report)
	}
	if _, e := os.Stat(file); e != nil {
		t.Fatal(e)
	}
}
func TestCleanupAdministratorAndIdleGates(t *testing.T) {
	s, root, _ := fixture(t)
	s.cleanupToken = strings.Repeat("m", 64)
	call := func(route, body, key string) int {
		req := httptest.NewRequest("POST", route, strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+s.token)
		if key != "" {
			req.Header.Set("X-DMT-Maintenance", key)
		}
		out := httptest.NewRecorder()
		s.ServeHTTP(out, req)
		return out.Code
	}
	encoded, _ := json.Marshal(map[string]any{"roots": []string{root}})
	if code := call("/maintenance/preview", string(encoded), ""); code != 403 {
		t.Fatal(code)
	}
	s.jobs["active"] = &Job{State: "running"}
	if code := call("/maintenance/preview", string(encoded), s.cleanupToken); code != 409 {
		t.Fatal(code)
	}
	delete(s.jobs, "active")
	if code := call("/maintenance/delete", `{"plan":"unknown"}`, s.cleanupToken); code != 409 {
		t.Fatal(code)
	}
	s.maintenance = true
	if code := call("/scan", string(encoded), ""); code != 409 {
		t.Fatal(code)
	}
}

func TestCleanupCannotDeleteWithoutWritableAudit(t *testing.T) {
	s, root, _ := fixture(t)
	file := filepath.Join(root, "old.txt")
	oldCleanupFile(t, file, time.Now().Add(-40*24*time.Hour))
	s.store = filepath.Join(filepath.Dir(root), "not-a-directory")
	if e := os.WriteFile(s.store, []byte("blocked"), 0600); e != nil {
		t.Fatal(e)
	}
	plan := cleanupPlan(t, s, root)
	if len(plan.Files) != 1 {
		t.Fatal(plan)
	}
	report := s.deleteCleanupPlan(context.Background(), plan, func(CleanupReport) {})
	if report.Deleted != 0 || !strings.HasPrefix(report.Error, "AUDIT_UNAVAILABLE") {
		t.Fatal(report)
	}
	if _, e := os.Stat(file); e != nil {
		t.Fatal(e)
	}
}
