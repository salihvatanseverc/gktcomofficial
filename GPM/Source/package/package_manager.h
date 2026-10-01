#pragma once

#include <string>
#include <vector>

#include "../runtime/python_runtime.h"

class PackageManager
{
public:
    PackageManager();

    int listPackages() const;
    int install(const std::string& package) const;
    int uninstall(const std::string& package) const;
    int update(const std::string& package) const;
    int search(const std::string& package) const;
    int info(const std::string& package) const;
    int status() const;

private:
    bool isValidPackageName(const std::string& package) const;

    int runPip(
        const std::vector<std::string>& arguments
    ) const;

    PythonRuntime pythonRuntime;
};