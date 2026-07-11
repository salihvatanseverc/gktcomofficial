#include <windows.h>

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <optional>
#include <string>
#include <vector>

#include "json.hpp"

namespace fs = std::filesystem;
using json = nlohmann::json;

struct Runtime {
    std::wstring name;
    fs::path executable;
    std::vector<std::wstring> arguments;
};

std::wstring quote(const fs::path& value) {
    return L"\"" + value.wstring() + L"\"";
}

std::string lower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(),
                   [](unsigned char character) { return static_cast<char>(std::tolower(character)); });
    return value;
}

std::wstring asWide(const std::string& value) {
    return std::wstring(value.begin(), value.end());
}

std::optional<fs::path> findProjectRoot() {
    std::vector<fs::path> startingPoints{fs::current_path()};

    wchar_t executablePath[MAX_PATH]{};
    if (GetModuleFileNameW(nullptr, executablePath, MAX_PATH) != 0) {
        startingPoints.emplace_back(executablePath);
        startingPoints.back() = startingPoints.back().parent_path();
    }

    for (auto directory : startingPoints) {
        while (!directory.empty()) {
            if (fs::is_directory(directory / L"Language")) {
                return directory;
            }
            const fs::path parent = directory.parent_path();
            if (parent == directory) {
                break;
            }
            directory = parent;
        }
    }
    return std::nullopt;
}

std::optional<Runtime> findRuntime(const fs::path& projectRoot,
                                   const fs::path& script) {
    const std::string extension = lower(script.extension().string());
    const fs::path languageDirectory = projectRoot / L"Language";

    for (const auto& entry : fs::directory_iterator(languageDirectory)) {
        if (!entry.is_directory()) {
            continue;
        }

        const fs::path manifestPath = entry.path() / L"manifest.json";
        if (!fs::is_regular_file(manifestPath)) {
            continue;
        }

        try {
            std::ifstream manifest(manifestPath);
            const json data = json::parse(manifest);
            if (data.value("type", "") != "runtime" ||
                !data.contains("extensions") || !data.contains("entry")) {
                continue;
            }

            bool supportsScript = false;
            for (const auto& supportedExtension : data.at("extensions")) {
                if (lower(supportedExtension.get<std::string>()) == extension) {
                    supportsScript = true;
                    break;
                }
            }

            const std::string entryValue = data.at("entry").get<std::string>();
            const fs::path bundledExecutable = entry.path() / fs::path(entryValue);
            const fs::path executable = fs::is_regular_file(bundledExecutable)
                ? bundledExecutable : fs::path(entryValue);
            if (supportsScript) {
                const std::string name = data.value("name", std::string{"Unknown runtime"});
                std::vector<std::wstring> arguments;
                for (const auto& argument : data.value("arguments", json::array())) {
                    arguments.push_back(asWide(argument.get<std::string>()));
                }
                return Runtime{asWide(name), executable, arguments};
            }
        } catch (const json::exception&) {
            std::wcerr << L"Gecersiz manifest atlandi: " << manifestPath << L'\n';
        }
    }
    return std::nullopt;
}

int wmain(int argumentCount, wchar_t* arguments[]) {
    if (argumentCount != 2) {
        std::wcerr << L"Kullanim: Gokturk.exe <calistirilacak-dosya>\n";
        return 1;
    }

    const fs::path script = fs::absolute(arguments[1]);
    if (!fs::is_regular_file(script)) {
        std::wcerr << L"Dosya bulunamadi: " << script << L'\n';
        return 1;
    }

    const auto projectRoot = findProjectRoot();
    if (!projectRoot) {
        std::wcerr << L"Language klasoru bulunamadi.\n";
        return 1;
    }

    const auto runtime = findRuntime(*projectRoot, script);
    if (!runtime) {
        std::wcerr << L"Bu dosya uzantisi icin bir calisma zamani bulunamadi: "
                   << script.extension() << L'\n';
        return 1;
    }

    std::wstring command = quote(runtime->executable);
    if (runtime->arguments.empty()) {
        command += L" " + quote(script);
    } else {
        for (std::wstring argument : runtime->arguments) {
            const size_t placeholder = argument.find(L"{script}");
            if (placeholder != std::wstring::npos) {
                argument.replace(placeholder, std::wstring{L"{script}"}.size(), script.wstring());
            }
            command += L" \"" + argument + L"\"";
        }
    }
    std::vector<wchar_t> commandBuffer(command.begin(), command.end());
    commandBuffer.push_back(L'\0');

    STARTUPINFOW startupInfo{};
    startupInfo.cb = sizeof(startupInfo);
    PROCESS_INFORMATION processInfo{};
    const std::wstring workingDirectory = script.parent_path().wstring();

    if (!CreateProcessW(nullptr, commandBuffer.data(), nullptr, nullptr, FALSE, 0,
                        nullptr, workingDirectory.c_str(), &startupInfo, &processInfo)) {
        std::wcerr << L"'" << runtime->name << L"' baslatilamadi. Hata kodu: "
                   << GetLastError() << L'\n';
        return 1;
    }

    WaitForSingleObject(processInfo.hProcess, INFINITE);
    DWORD exitCode = 1;
    GetExitCodeProcess(processInfo.hProcess, &exitCode);
    CloseHandle(processInfo.hThread);
    CloseHandle(processInfo.hProcess);

    return static_cast<int>(exitCode);
}
