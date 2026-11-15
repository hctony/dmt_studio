package main

import (
	"os"
	"syscall"
	"time"
)

func newestTime(info os.FileInfo) time.Time {
	stamp := info.ModTime()
	if data, ok := info.Sys().(*syscall.Win32FileAttributeData); ok {
		created := time.Unix(0, data.CreationTime.Nanoseconds())
		if created.After(stamp) {
			stamp = created
		}
	}
	return stamp
}
