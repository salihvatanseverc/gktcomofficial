package runtime

import (
	"gokturk/logger/Logger"
)

type Runtime struct {
	logger *logger.Logger
}

func New() *Runtime {
	return &Runtime{
		logger: logger.New(),
	}
}

func (r *Runtime) Start() error {
	return r.logger.Runtime("Go Runtime başlatıldı.")
}

func (r *Runtime) Stop() error {
	return r.logger.Runtime("Go Runtime durduruldu.")
}

func (r *Runtime) Error(message string) error {
	return r.logger.Error(message)
}

func (r *Runtime) Warning(message string) error {
	return r.logger.Warning(message)
}

func (r *Runtime) Debug(message string) error {
	return r.logger.Debug(message)
}