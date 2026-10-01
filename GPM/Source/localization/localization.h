#pragma once

#include <string>

enum class Language
{
    Turkish,
    Bulgarian,
    English
};

class Localization
{
public:
    Localization();

    void setLanguage(Language language);
    Language language() const;

    const std::string& title() const;

    const std::string& menuPackages() const;
    const std::string& menuInstall() const;
    const std::string& menuUninstall() const;
    const std::string& menuLanguages() const;
    const std::string& menuExit() const;
    const std::string& selection() const;

    const std::string& packageName() const;
    const std::string& packageInstalling() const;
    const std::string& packageInstalled() const;
    const std::string& packageInstallFailed() const;

    const std::string& packageUninstalling() const;
    const std::string& packageUninstalled() const;
    const std::string& packageUninstallFailed() const;

    const std::string& packageNameMissing() const;
    const std::string& pressEnter() const;
    const std::string& invalidSelection() const;

    const std::string& languageTurkish() const;
    const std::string& languageBulgarian() const;
    const std::string& languageEnglish() const;
    const std::string& languageSelected() const;

    const std::string& packageSearchTitle() const;
    const std::string& packageInfoTitle() const;
    const std::string& packageStatusTitle() const;

    const std::string& packageSearching() const;
    const std::string& packageSearchFailed() const;

    const std::string& packageInfoLoading() const;
    const std::string& packageInfoFailed() const;

    const std::string& pythonRuntimeMissing() const;
    const std::string& invalidPackageName() const;

    const std::string& commandHelp() const;
    const std::string& commandVersion() const;

private:
    void updateTexts();

    Language currentLanguage;

    std::string textTitle;

    std::string textMenuPackages;
    std::string textMenuInstall;
    std::string textMenuUninstall;
    std::string textMenuLanguages;
    std::string textMenuExit;
    std::string textSelection;

    std::string textPackageName;
    std::string textPackageInstalling;
    std::string textPackageInstalled;
    std::string textPackageInstallFailed;

    std::string textPackageUninstalling;
    std::string textPackageUninstalled;
    std::string textPackageUninstallFailed;

    std::string textPackageNameMissing;
    std::string textPressEnter;
    std::string textInvalidSelection;

    std::string textLanguageTurkish;
    std::string textLanguageBulgarian;
    std::string textLanguageEnglish;
    std::string textLanguageSelected;

    std::string textPackageSearchTitle;
    std::string textPackageInfoTitle;
    std::string textPackageStatusTitle;

    std::string textPackageSearching;
    std::string textPackageSearchFailed;

    std::string textPackageInfoLoading;
    std::string textPackageInfoFailed;

    std::string textPythonRuntimeMissing;
    std::string textInvalidPackageName;

    std::string textCommandHelp;
    std::string textCommandVersion;
};