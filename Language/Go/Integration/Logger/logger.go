package logger

import (
	"fmt"
	"os"
	"path/filepath"
	"time"
)

type Level string

const (
	INFO    Level = "INFO"
	WARNING Level = "WARNING"
	ERROR   Level = "ERROR"
	DEBUG   Level = "DEBUG"
)

type Logger struct {
	logDir string
}

func New() *Logger {
	return &Logger{
		logDir: filepath.Join("..", "Logs"),
	}
}

func (l *Logger) Write(fileName string, level Level, message string) error {
	if err := os.MkdirAll(l.logDir, 0755); err != nil {
		return err
	}

	file, err := os.OpenFile(
		filepath.Join(l.logDir, fileName),
		os.O_CREATE|os.O_APPEND|os.O_WRONLY,
		0644,
	)
	if err != nil {
		return err
	}
	defer file.Close()

	timestamp := time.Now().Format("2006-01-02 15:04:05")

	_, err = fmt.Fprintf(
		file,
		"[%s] [%s] %s\n",
		timestamp,
		level,
		message,
	)

	return err
}

func (l *Logger) Runtime(message string) error {
	return l.Write("runtime.log", INFO, message)
}

func (l *Logger) Error(message string) error {
	return l.Write("errors.log", ERROR, message)
}

func (l *Logger) Warning(message string) error {
	return l.Write("runtime.log", WARNING, message)
}

func (l *Logger) Debug(message string) error {
	return l.Write("runtime.log", DEBUG, message)
}