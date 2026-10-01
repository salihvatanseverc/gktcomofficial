#include "core/gpm_core.h"
#include "package/package_manager.h"

#include <iostream>
#include <string>

void printHelp()
{
    std::cout << "\n";
    std::cout << "Göktürk Package Manager\n";
    std::cout << "=======================\n\n";

    std::cout << "gpm\n";
    std::cout << "gpm list\n";
    std::cout << "gpm install <paket>\n";
    std::cout << "gpm uninstall <paket>\n";
    std::cout << "gpm update <paket>\n";
    std::cout << "gpm search <paket>\n";
    std::cout << "gpm info <paket>\n";
    std::cout << "gpm status\n";
    std::cout << "gpm help\n";
    std::cout << "gpm version\n\n";
}

void printVersion()
{
    std::cout << "Göktürk Package Manager\n";
    std::cout << "Version 1.0.0\n";
}

int main(int argc, char* argv[])
{
    PackageManager manager;

    if (argc == 1)
    {
        GPMCore core;
        core.run();

        return 0;
    }

    const std::string command = argv[1];

    if (command == "list")
    {
        return manager.listPackages();
    }

    if (command == "install")
    {
        if (argc < 3)
        {
            std::cout << "Paket adi belirtilmedi.\n";
            return 1;
        }

        return manager.install(argv[2]);
    }

    if (command == "uninstall")
    {
        if (argc < 3)
        {
            std::cout << "Paket adi belirtilmedi.\n";
            return 1;
        }

        return manager.uninstall(argv[2]);
    }

    if (command == "update")
    {
        if (argc < 3)
        {
            std::cout << "Paket adi belirtilmedi.\n";
            return 1;
        }

        return manager.update(argv[2]);
    }

    if (command == "search")
    {
        if (argc < 3)
        {
            std::cout << "Paket adi belirtilmedi.\n";
            return 1;
        }

        return manager.search(argv[2]);
    }

    if (command == "info")
    {
        if (argc < 3)
        {
            std::cout << "Paket adi belirtilmedi.\n";
            return 1;
        }

        return manager.info(argv[2]);
    }

    if (command == "status")
    {
        return manager.status();
    }

    if (command == "help")
    {
        printHelp();
        return 0;
    }

    if (command == "version")
    {
        printVersion();
        return 0;
    }

    std::cout << "Bilinmeyen komut: "
              << command
              << "\n\n";

    printHelp();

    return 1;
}