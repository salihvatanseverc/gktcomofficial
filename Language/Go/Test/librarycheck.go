package main

import (
    "fmt"
    "os"
    "path/filepath"
    "strings"
)

func main() {
    fmt.Println("=== Göktürk Go Kütüphane Testi ===")

    fmt.Println("[PASS] fmt")

    text := "Göktürk Programlama Dili"
    if strings.Contains(text, "Göktürk") {
        fmt.Println("[PASS] strings")
    } else {
        fmt.Println("[FAIL] strings")
    }

    currentDir, err := os.Getwd()
    if err != nil {
        fmt.Println("[FAIL] os:", err)
        return
    }
    fmt.Println("[PASS] os")
    fmt.Println("Çalışma dizini:", currentDir)

    filePath := filepath.Join(currentDir, "library_test.txt")
    fmt.Println("[PASS] filepath")

    err = os.WriteFile(filePath, []byte("Göktürk Go Kütüphane Testi"), 0644)
    if err != nil {
        fmt.Println("[FAIL] os.WriteFile:", err)
        return
    }

    data, err := os.ReadFile(filePath)
    if err != nil {
        fmt.Println("[FAIL] os.ReadFile:", err)
        return
    }

    if string(data) == "Göktürk Go Kütüphane Testi" {
        fmt.Println("[PASS] UTF-8 dosya okuma/yazma")
    } else {
        fmt.Println("[FAIL] UTF-8 dosya okuma/yazma")
    }

    err = os.Remove(filePath)
    if err != nil {
        fmt.Println("[WARNING] Dosya silinemedi:", err)
    } else {
        fmt.Println("[PASS] Dosya temizliği")
    }

    fmt.Println("=== Kütüphane testi tamamlandı ===")
}
