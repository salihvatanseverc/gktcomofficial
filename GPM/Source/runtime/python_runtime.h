#pragma once

#include <string>

class PythonRuntime
{
public:
    PythonRuntime();

    const std::string& executable() const;
    bool isAvailable() const;

private:
    std::string pythonExecutable;
};