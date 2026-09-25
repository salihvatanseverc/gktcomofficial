#include <cstdlib>
#include <iostream>
#include <string>

int main(int argc, char* argv[])
{
    if (argc < 2)
    {
        std::cout << "Göktürk Package Manager\n\n";
        std::cout << "Kullanim:\n";
        std::cout << "  gpm install <paket>\n";
        std::cout << "  gpm uninstall <paket>\n";
        std::cout << "  gpm update <paket>\n";
        std::cout << "  gpm packages\n";
        return 0;
    }

    const std::string command = argv[1];

    const std::string python =
        R"(..\Language\Python\Windows Runtime\python.exe)";

    if (command == "install")
    {
        if (argc < 3)
        {
            std::cout << "Paket adi belirtilmedi.\n";
            return 1;
        }

        std::string package = argv[2];

        std::string commandLine =
            "\"" + python + "\" -m pip install " + package;

        return std::system(commandLine.c_str());
    }

    if (command == "uninstall")
    {
        if (argc < 3)
        {
            std::cout << "Paket adi belirtilmedi.\n";
            return 1;
        }

        std::string package = argv[2];

        std::string commandLine =
            "\"" + python + "\" -m pip uninstall " + package + " -y";

        return std::system(commandLine.c_str());
    }

    if (command == "update")
    {
        if (argc < 3)
        {
            std::cout << "Paket adi belirtilmedi.\n";
            return 1;
        }

        std::string package = argv[2];

        std::string commandLine =
            "\"" + python + "\" -m pip install --upgrade " + package;

        return std::system(commandLine.c_str());
    }

    if (command == "packages")
    {
        std::string commandLine =
            "\"" + python + "\" -m pip list";

        return std::system(commandLine.c_str());
    }

    std::cout << "Bilinmeyen komut: " << command << "\n";
    return 1;
}