#include "package_manager.h"

#include <cctype>
#include <iostream>
#include <process.h>

PackageManager::PackageManager() = default;

bool PackageManager::isValidPackageName(
    const std::string& package) const
{
    if (package.empty() || package.size() > 200)
    {
        return false;
    }

    for (unsigned char character : package)
    {
        if (std::isalnum(character))
        {
            continue;
        }

        switch (character)
        {
        case '-':
        case '_':
        case '.':
        case '<':
        case '>':
        case '=':
        case '!':
        case '~':
        case ',':
            continue;

        default:
            return false;
        }
    }

    return true;
}

int PackageManager::runPip(
    const std::vector<std::string>& arguments) const
{
    if (!pythonRuntime.isAvailable())
    {
        std::cout << "Python Runtime bulunamadi.\n";
        std::cout << "Aranan yol: "
                  << pythonRuntime.executable()
                  << '\n';

        return 1;
    }

    std::vector<std::string> command;

    command.push_back(pythonRuntime.executable());
    command.push_back("-m");
    command.push_back("pip");

    for (const auto& argument : arguments)
    {
        command.push_back(argument);
    }

    std::vector<char*> argv;

    for (auto& argument : command)
    {
        argv.push_back(argument.data());
    }

    argv.push_back(nullptr);

    return _spawnv(
        _P_WAIT,
        pythonRuntime.executable().c_str(),
        argv.data()
    );
}

int PackageManager::listPackages() const
{
    return runPip({
        "list"
    });
}

int PackageManager::install(
    const std::string& package) const
{
    if (!isValidPackageName(package))
    {
        std::cout << "Gecersiz paket adi.\n";
        return 1;
    }

    return runPip({
        "install",
        package
    });
}

int PackageManager::uninstall(
    const std::string& package) const
{
    if (!isValidPackageName(package))
    {
        std::cout << "Gecersiz paket adi.\n";
        return 1;
    }

    return runPip({
        "uninstall",
        package,
        "-y"
    });
}

int PackageManager::update(
    const std::string& package) const
{
    if (!isValidPackageName(package))
    {
        std::cout << "Gecersiz paket adi.\n";
        return 1;
    }

    return runPip({
        "install",
        "--upgrade",
        package
    });
}

int PackageManager::search(
    const std::string& package) const
{
    if (!isValidPackageName(package))
    {
        std::cout << "Gecersiz paket adi.\n";
        return 1;
    }

    return runPip({
        "index",
        "versions",
        package
    });
}

int PackageManager::info(
    const std::string& package) const
{
    if (!isValidPackageName(package))
    {
        std::cout << "Gecersiz paket adi.\n";
        return 1;
    }

    return runPip({
        "show",
        package
    });
}

int PackageManager::status() const
{
    if (!pythonRuntime.isAvailable())
    {
        std::cout << "Python Runtime bulunamadi.\n";
        return 1;
    }

    std::cout << "Python Runtime\n";
    std::cout << "==============\n";
    std::cout << "Konum: "
              << pythonRuntime.executable()
              << "\n\n";

    return runPip({
        "--version"
    });
}