//go:build !windows

package main

import (
	"context"
	"fmt"
	"os"
	"time"
)

func cleanupIdentity(path string) (string, os.FileInfo, error) {
	return "", nil, fmt.Errorf("SOURCE_DELETE_REQUIRES_WINDOWS")
}
func isCleanupLink(info os.FileInfo) bool { return info.Mode()&os.ModeSymlink != 0 }

func removeVerified(ctx context.Context, file CleanupFile, cutoff time.Time) error {
	return fmt.Errorf("SOURCE_DELETE_REQUIRES_WINDOWS")
}
