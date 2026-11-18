package main

import (
	"context"
	"fmt"
	"os"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

var cleanupKernel = syscall.NewLazyDLL("kernel32.dll")
var cleanupFinalPath = cleanupKernel.NewProc("GetFinalPathNameByHandleW")
var cleanupDisposition = cleanupKernel.NewProc("SetFileInformationByHandle")

func isCleanupLink(info os.FileInfo) bool {
	if info.Mode()&os.ModeSymlink != 0 {
		return true
	}
	if data, ok := info.Sys().(*syscall.Win32FileAttributeData); ok {
		return data.FileAttributes&syscall.FILE_ATTRIBUTE_REPARSE_POINT != 0
	}
	return false
}

func cleanupHandleIdentity(handle syscall.Handle) (string, error) {
	var data syscall.ByHandleFileInformation
	if e := syscall.GetFileInformationByHandle(handle, &data); e != nil {
		return "", e
	}
	if data.FileIndexHigh == 0 && data.FileIndexLow == 0 {
		return "", fmt.Errorf("FILE_ID_UNAVAILABLE")
	}
	return fmt.Sprintf("%08x:%08x%08x", data.VolumeSerialNumber, data.FileIndexHigh, data.FileIndexLow), nil
}
func cleanupIdentity(path string) (string, os.FileInfo, error) {
	name, e := syscall.UTF16PtrFromString(path)
	if e != nil {
		return "", nil, e
	}
	handle, e := syscall.CreateFile(name, 0x80, syscall.FILE_SHARE_READ|syscall.FILE_SHARE_WRITE|syscall.FILE_SHARE_DELETE, nil, syscall.OPEN_EXISTING, 0x00200000, 0)
	if e != nil {
		return "", nil, e
	}
	opened := os.NewFile(uintptr(handle), path)
	defer opened.Close()
	identity, e := cleanupHandleIdentity(handle)
	if e != nil {
		return "", nil, e
	}
	info, e := opened.Stat()
	return identity, info, e
}

func removeVerified(ctx context.Context, file CleanupFile, cutoff time.Time) error {
	if e := ctx.Err(); e != nil {
		return e
	}
	name, e := syscall.UTF16PtrFromString(file.Path)
	if e != nil {
		return e
	}
	// Deny writers/deleters while checking and deleting this exact file handle.
	handle, e := syscall.CreateFile(name, 0x00010000|0x80, syscall.FILE_SHARE_READ, nil, syscall.OPEN_EXISTING, 0x00200000, 0)
	if e != nil {
		return e
	}
	opened := os.NewFile(uintptr(handle), file.Path)
	defer opened.Close()
	if e := ctx.Err(); e != nil {
		return e
	}
	identity, e := cleanupHandleIdentity(handle)
	if e != nil {
		return e
	}
	if identity != file.Identity {
		return errCleanupChanged
	}
	info, e := opened.Stat()
	if e != nil {
		return e
	}
	if e = verifyCleanupFile(file, info, cutoff); e != nil {
		return e
	}
	buffer := make([]uint16, 32768)
	n, _, err := cleanupFinalPath.Call(uintptr(handle), uintptr(unsafe.Pointer(&buffer[0])), uintptr(len(buffer)), 0)
	if n == 0 || n >= uintptr(len(buffer)) {
		return fmt.Errorf("FINAL_PATH_UNVERIFIED: %v", err)
	}
	final := syscall.UTF16ToString(buffer[:n])
	if strings.HasPrefix(final, `\\?\UNC\`) {
		final = `\\` + strings.TrimPrefix(final, `\\?\UNC\`)
	} else {
		final = strings.TrimPrefix(final, `\\?\`)
	}
	if cleanupKey(final) != cleanupKey(file.Path) {
		return fmt.Errorf("HANDLE_PATH_CHANGED")
	}
	flag := byte(1)
	if e := ctx.Err(); e != nil {
		return e
	}
	ok, _, err := cleanupDisposition.Call(uintptr(handle), 4, uintptr(unsafe.Pointer(&flag)), 1)
	if ok == 0 {
		return fmt.Errorf("DELETE_FAILED: %v", err)
	}
	return opened.Close()
}
