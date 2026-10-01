#include "python_runtime.h"

#include <filesystem>
#include <windows.h>

PythonRuntime::PythonRuntime()
{
    char buffer[MAX_PATH];

    const DWORD length =
        GetModuleFileNameA(
            nullptr,
            buffer,
            MAX_PATH
        );

    if (length == 0 || length >= MAX_PATH)
    {
        return;
    }

    std::filesystem::path gpmPath(
        std::string(buffer, length)
    );

    const std::filesystem::path projectRoot =
        gpmPath.parent_path().parent_path();

    pythonExecutable =
        (
            projectRoot /
            "Language" /
            "Python" /
            "Windows Runtime" /
            "python.exe"
        ).string();
}

const std::string& PythonRuntime::executable() const
{
    return pythonExecutable;
}

bool PythonRuntime::isAvailable() const
{
    return !pythonExecutable.empty() &&
           std::filesystem::exists(pythonExecutable);
}