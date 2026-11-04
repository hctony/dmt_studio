package main

import (
	"path/filepath"
	"testing"
)

func TestSaveLoadAndReplace(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config", "profiles.json")
	first := State{Profiles: []Profile{{Name: "작업", Model: "M1", Lot: "LOT-1", OutputDir: `D:\결과`}}, Active: "작업"}
	if err := save(path, first); err != nil {
		t.Fatal(err)
	}
	got, err := load(path)
	if err != nil {
		t.Fatal(err)
	}
	if got.Active != first.Active || len(got.Profiles) != 1 || got.Profiles[0] != first.Profiles[0] {
		t.Fatalf("unexpected saved state: %+v", got)
	}
	first.Profiles[0].Lot = "LOT-2"
	if err := save(path, first); err != nil {
		t.Fatal(err)
	}
	got, err = load(path)
	if err != nil {
		t.Fatal(err)
	}
	if got.Profiles[0].Lot != "LOT-2" {
		t.Fatalf("replacement failed: %+v", got)
	}
}
