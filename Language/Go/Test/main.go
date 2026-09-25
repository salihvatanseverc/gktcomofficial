package main

import (
	"log"

	runtime "gokturk/logger/Runtime"
)

func main() {
	rt := runtime.New()

	if err := rt.Start(); err != nil {
		log.Fatal(err)
	}

	if err := rt.Debug("Go Runtime entegrasyon işlemi başlatıldı."); err != nil {
		log.Fatal(err)
	}

	if err := rt.Stop(); err != nil {
		log.Fatal(err)
	}
}