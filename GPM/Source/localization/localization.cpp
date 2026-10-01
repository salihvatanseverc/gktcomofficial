#include "localization.h"

Localization::Localization()
    : currentLanguage(Language::Turkish)
{
    updateTexts();
}

void Localization::setLanguage(Language language)
{
    currentLanguage = language;
    updateTexts();
}

Language Localization::language() const
{
    return currentLanguage;
}

void Localization::updateTexts()
{
    switch (currentLanguage)
    {
    case Language::Turkish:
        textTitle = "Göktürk Package Manager";

        textMenuPackages = "Paketlerim";
        textMenuInstall = "Paket Yükle";
        textMenuUninstall = "Paket Sil";
        textMenuLanguages = "Dil Seçenekleri";
        textMenuExit = "Çıkış";
        textSelection = "Seçiminiz: ";

        textPackageName = "Paket adı: ";
        textPackageInstalling = "Paket yükleniyor...";
        textPackageInstalled = "Paket başarıyla yüklendi.";
        textPackageInstallFailed = "Paket yüklenemedi.";

        textPackageUninstalling = "Paket kaldırılıyor...";
        textPackageUninstalled = "Paket başarıyla kaldırıldı.";
        textPackageUninstallFailed = "Paket kaldırılamadı.";

        textPackageNameMissing = "Paket adı belirtilmedi.";
        textPressEnter = "Devam etmek için Enter'a basın...";
        textInvalidSelection = "Geçersiz seçim.";

        textLanguageTurkish = "Türkçe";
        textLanguageBulgarian = "Български";
        textLanguageEnglish = "English";
        textLanguageSelected = "Türkçe seçildi.";

        textPackageSearchTitle = "Python Paket Arama";
        textPackageInfoTitle = "Python Paket Bilgisi";
        textPackageStatusTitle = "GPM Durumu";

        textPackageSearching = "Paket aranıyor...";
        textPackageSearchFailed = "Paket aranamadı.";

        textPackageInfoLoading = "Paket bilgileri yükleniyor...";
        textPackageInfoFailed = "Paket bilgileri alınamadı.";

        textPythonRuntimeMissing = "Python Runtime bulunamadı.";
        textInvalidPackageName = "Geçersiz paket adı.";

        textCommandHelp = "Yardım";
        textCommandVersion = "Sürüm";

        break;

    case Language::Bulgarian:
        textTitle = "Göktürk Package Manager";

        textMenuPackages = "Моите пакети";
        textMenuInstall = "Инсталиране на пакет";
        textMenuUninstall = "Премахване на пакет";
        textMenuLanguages = "Езикови опции";
        textMenuExit = "Изход";
        textSelection = "Вашият избор: ";

        textPackageName = "Име на пакета: ";
        textPackageInstalling = "Инсталиране на пакета...";
        textPackageInstalled =
            "Пакетът беше инсталиран успешно.";
        textPackageInstallFailed =
            "Пакетът не може да бъде инсталиран.";

        textPackageUninstalling =
            "Премахване на пакета...";
        textPackageUninstalled =
            "Пакетът беше премахнат успешно.";
        textPackageUninstallFailed =
            "Пакетът не може да бъде премахнат.";

        textPackageNameMissing =
            "Не е посочено име на пакет.";

        textPressEnter =
            "Натиснете Enter, за да продължите...";

        textInvalidSelection =
            "Невалиден избор.";

        textLanguageTurkish = "Türkçe";
        textLanguageBulgarian = "Български";
        textLanguageEnglish = "English";
        textLanguageSelected =
            "Българският език е избран.";

        textPackageSearchTitle =
            "Търсене на Python пакет";

        textPackageInfoTitle =
            "Информация за Python пакет";

        textPackageStatusTitle =
            "Състояние на GPM";

        textPackageSearching =
            "Търсене на пакет...";

        textPackageSearchFailed =
            "Пакетът не може да бъде намерен.";

        textPackageInfoLoading =
            "Зареждане на информацията за пакета...";

        textPackageInfoFailed =
            "Информацията за пакета не може да бъде получена.";

        textPythonRuntimeMissing =
            "Python Runtime не е намерен.";

        textInvalidPackageName =
            "Невалидно име на пакет.";

        textCommandHelp = "Помощ";
        textCommandVersion = "Версия";

        break;

    case Language::English:
        textTitle = "Göktürk Package Manager";

        textMenuPackages = "My Packages";
        textMenuInstall = "Install Package";
        textMenuUninstall = "Uninstall Package";
        textMenuLanguages = "Language Options";
        textMenuExit = "Exit";
        textSelection = "Selection: ";

        textPackageName = "Package name: ";
        textPackageInstalling = "Installing package...";
        textPackageInstalled =
            "Package installed successfully.";
        textPackageInstallFailed =
            "Package installation failed.";

        textPackageUninstalling =
            "Removing package...";
        textPackageUninstalled =
            "Package removed successfully.";
        textPackageUninstallFailed =
            "Package removal failed.";

        textPackageNameMissing =
            "Package name was not specified.";

        textPressEnter =
            "Press Enter to continue...";

        textInvalidSelection =
            "Invalid selection.";

        textLanguageTurkish = "Türkçe";
        textLanguageBulgarian = "Български";
        textLanguageEnglish = "English";
        textLanguageSelected =
            "English selected.";

        textPackageSearchTitle =
            "Python Package Search";

        textPackageInfoTitle =
            "Python Package Information";

        textPackageStatusTitle =
            "GPM Status";

        textPackageSearching =
            "Searching package...";

        textPackageSearchFailed =
            "Package search failed.";

        textPackageInfoLoading =
            "Loading package information...";

        textPackageInfoFailed =
            "Failed to retrieve package information.";

        textPythonRuntimeMissing =
            "Python Runtime was not found.";

        textInvalidPackageName =
            "Invalid package name.";

        textCommandHelp = "Help";
        textCommandVersion = "Version";

        break;
    }
}

const std::string& Localization::title() const
{
    return textTitle;
}

const std::string& Localization::menuPackages() const
{
    return textMenuPackages;
}

const std::string& Localization::menuInstall() const
{
    return textMenuInstall;
}

const std::string& Localization::menuUninstall() const
{
    return textMenuUninstall;
}

const std::string& Localization::menuLanguages() const
{
    return textMenuLanguages;
}

const std::string& Localization::menuExit() const
{
    return textMenuExit;
}

const std::string& Localization::selection() const
{
    return textSelection;
}

const std::string& Localization::packageName() const
{
    return textPackageName;
}

const std::string& Localization::packageInstalling() const
{
    return textPackageInstalling;
}

const std::string& Localization::packageInstalled() const
{
    return textPackageInstalled;
}

const std::string& Localization::packageInstallFailed() const
{
    return textPackageInstallFailed;
}

const std::string& Localization::packageUninstalling() const
{
    return textPackageUninstalling;
}

const std::string& Localization::packageUninstalled() const
{
    return textPackageUninstalled;
}

const std::string& Localization::packageUninstallFailed() const
{
    return textPackageUninstallFailed;
}

const std::string& Localization::packageNameMissing() const
{
    return textPackageNameMissing;
}

const std::string& Localization::pressEnter() const
{
    return textPressEnter;
}

const std::string& Localization::invalidSelection() const
{
    return textInvalidSelection;
}

const std::string& Localization::languageTurkish() const
{
    return textLanguageTurkish;
}

const std::string& Localization::languageBulgarian() const
{
    return textLanguageBulgarian;
}

const std::string& Localization::languageEnglish() const
{
    return textLanguageEnglish;
}

const std::string& Localization::languageSelected() const
{
    return textLanguageSelected;
}

const std::string& Localization::packageSearchTitle() const
{
    return textPackageSearchTitle;
}

const std::string& Localization::packageInfoTitle() const
{
    return textPackageInfoTitle;
}

const std::string& Localization::packageStatusTitle() const
{
    return textPackageStatusTitle;
}

const std::string& Localization::packageSearching() const
{
    return textPackageSearching;
}

const std::string& Localization::packageSearchFailed() const
{
    return textPackageSearchFailed;
}

const std::string& Localization::packageInfoLoading() const
{
    return textPackageInfoLoading;
}

const std::string& Localization::packageInfoFailed() const
{
    return textPackageInfoFailed;
}

const std::string& Localization::pythonRuntimeMissing() const
{
    return textPythonRuntimeMissing;
}

const std::string& Localization::invalidPackageName() const
{
    return textInvalidPackageName;
}

const std::string& Localization::commandHelp() const
{
    return textCommandHelp;
}

const std::string& Localization::commandVersion() const
{
    return textCommandVersion;
}