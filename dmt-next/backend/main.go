package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// Profiles contain user-entered paths and identifiers, never credentials.
type Profile struct {
	Name        string `json:"name"`
	Model       string `json:"model"`
	Lot         string `json:"lot"`
	NasRoot     string `json:"nasRoot"`
	BackupDir   string `json:"backupDir"`
	SkipdataDir string `json:"skipdataDir"`
	OutputDir   string `json:"outputDir"`
}
type State struct {
	Profiles []Profile `json:"profiles"`
	Active   string    `json:"active"`
}

func statePath() (string, error) {
	dir, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "DMT Analysis Next", "profiles.json"), nil
}
func load(path string) (State, error) {
	var s State
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return State{Profiles: []Profile{}}, nil
	}
	if err != nil {
		return s, err
	}
	err = json.Unmarshal(data, &s)
	return s, err
}
func save(path string, s State) error {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	// Write a temporary sibling first so an interrupted write cannot truncate the previous file.
	f, err := os.CreateTemp(filepath.Dir(path), "profiles-*.tmp")
	if err != nil {
		return err
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	if _, err = f.Write(data); err != nil {
		f.Close()
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}
func run(args []string) error {
	if len(args) != 1 {
		return errors.New("expected load or save")
	}
	path, err := statePath()
	if err != nil {
		return err
	}
	switch args[0] {
	case "load":
		s, err := load(path)
		if err != nil {
			return err
		}
		return json.NewEncoder(os.Stdout).Encode(s)
	case "save":
		var s State
		d := json.NewDecoder(os.Stdin)
		d.DisallowUnknownFields()
		if err = d.Decode(&s); err != nil {
			return err
		}
		names := map[string]bool{}
		for _, p := range s.Profiles {
			n := strings.TrimSpace(p.Name)
			if n == "" || names[n] {
				return errors.New("profile names must be unique and nonempty")
			}
			names[n] = true
		}
		if s.Active != "" && !names[s.Active] {
			return errors.New("active profile does not exist")
		}
		return save(path, s)
	default:
		return errors.New("unknown command")
	}
}
func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
