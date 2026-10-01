#include "gpm_core.h"

#include "../package/package_manager.h"
#include "../localization/localization.h"

#include <iostream>
#include <string>

void GPMCore::run()
{
    PackageManager manager;
    Localization localization;

    while (true)
    {
        std::cout << "\n";
        std::cout << localization.title() << "\n";
        std::cout << "=======================\n\n";

        std::cout << "[1] "
                  << localization.menuPackages()
                  << "\n";

        std::cout << "[2] "
                  << localization.menuInstall()
                  << "\n";

        std::cout << "[3] "
                  << localization.menuUninstall()
                  << "\n";

        std::cout << "[4] "
                  << localization.menuLanguages()
                  << "\n";

        std::cout << "[0] "
                  << localization.menuExit()
                  << "\n\n";

        std::cout << localization.selection();

        std::string choice;
        std::getline(std::cin, choice);

        if (choice == "0")
        {
            break;
        }

        if (choice == "1")
        {
            manager.listPackages();

            std::cout << "\n"
                      << localization.pressEnter();

            std::string pause;
            std::getline(std::cin, pause);
        }
        else if (choice == "2")
        {
            std::string package;

            std::cout << "\n"
                      << localization.menuInstall()
                      << "\n";

            std::cout << "=======================\n\n";

            std::cout << localization.packageName();
            std::getline(std::cin, package);

            if (package.empty())
            {
                std::cout << "\n"
                          << localization.packageNameMissing()
                          << "\n";
            }
            else
            {
                std::cout << "\n"
                          << localization.packageInstalling()
                          << "\n\n";

                const int result =
                    manager.install(package);

                if (result == 0)
                {
                    std::cout << "\n"
                              << localization.packageInstalled()
                              << "\n";
                }
                else
                {
                    std::cout << "\n"
                              << localization.packageInstallFailed()
                              << "\n";
                }
            }

            std::cout << "\n"
                      << localization.pressEnter();

            std::string pause;
            std::getline(std::cin, pause);
        }
        else if (choice == "3")
        {
            std::string package;

            std::cout << "\n"
                      << localization.menuUninstall()
                      << "\n";

            std::cout << "=======================\n\n";

            std::cout << localization.packageName();
            std::getline(std::cin, package);

            if (package.empty())
            {
                std::cout << "\n"
                          << localization.packageNameMissing()
                          << "\n";
            }
            else
            {
                std::cout << "\n"
                          << localization.packageUninstalling()
                          << "\n\n";

                const int result =
                    manager.uninstall(package);

                if (result == 0)
                {
                    std::cout << "\n"
                              << localization.packageUninstalled()
                              << "\n";
                }
                else
                {
                    std::cout << "\n"
                              << localization.packageUninstallFailed()
                              << "\n";
                }
            }

            std::cout << "\n"
                      << localization.pressEnter();

            std::string pause;
            std::getline(std::cin, pause);
        }
        else if (choice == "4")
        {
            while (true)
            {
                std::cout << "\n";
                std::cout << localization.menuLanguages()
                          << "\n";

                std::cout << "===================\n\n";

                std::cout << "[1] "
                          << localization.languageTurkish()
                          << "\n";

                std::cout << "[2] "
                          << localization.languageBulgarian()
                          << "\n";

                std::cout << "[3] "
                          << localization.languageEnglish()
                          << "\n";

                std::cout << "[0] Geri\n\n";

                std::cout << localization.selection();

                std::string languageChoice;
                std::getline(
                    std::cin,
                    languageChoice
                );

                if (languageChoice == "0")
                {
                    break;
                }

                if (languageChoice == "1")
                {
                    localization.setLanguage(
                        Language::Turkish
                    );

                    std::cout << "\n"
                              << localization.languageSelected()
                              << "\n";

                    break;
                }

                if (languageChoice == "2")
                {
                    localization.setLanguage(
                        Language::Bulgarian
                    );

                    std::cout << "\n"
                              << localization.languageSelected()
                              << "\n";

                    break;
                }

                if (languageChoice == "3")
                {
                    localization.setLanguage(
                        Language::English
                    );

                    std::cout << "\n"
                              << localization.languageSelected()
                              << "\n";

                    break;
                }

                std::cout << "\n"
                          << localization.invalidSelection()
                          << "\n";
            }
        }
        else
        {
            std::cout << "\n"
                      << localization.invalidSelection()
                      << "\n";

            std::cout << "\n"
                      << localization.pressEnter();

            std::string pause;
            std::getline(std::cin, pause);
        }
    }
}