#define WIN32_LEAN_AND_MEAN
#define COBJMACROS
#include <windows.h>
#include <shlobj.h>
#include <objbase.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

HRESULT CreateShortcut(LPCWSTR lpszTarget, LPCWSTR lpszLinkFile, LPCWSTR lpszDesc, LPCWSTR lpszIcon, LPCWSTR lpszWorkDir) {
    IShellLinkW* psl = NULL;
    HRESULT hr = CoCreateInstance(&CLSID_ShellLink, NULL, CLSCTX_INPROC_SERVER, &IID_IShellLinkW, (void**)&psl);
    if (SUCCEEDED(hr)) {
        IPersistFile* ppf = NULL;
        IShellLinkW_SetPath(psl, lpszTarget);
        IShellLinkW_SetDescription(psl, lpszDesc);
        if (lpszWorkDir && wcslen(lpszWorkDir) > 0) {
            IShellLinkW_SetWorkingDirectory(psl, lpszWorkDir);
        }
        if (lpszIcon && wcslen(lpszIcon) > 0) {
            IShellLinkW_SetIconLocation(psl, lpszIcon, 0);
        }
        hr = IShellLinkW_QueryInterface(psl, &IID_IPersistFile, (void**)&ppf);
        if (SUCCEEDED(hr)) {
            hr = IPersistFile_Save(ppf, lpszLinkFile, TRUE);
            IPersistFile_Release(ppf);
        }
        IShellLinkW_Release(psl);
    }
    return hr;
}

int WINAPI WinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance, LPSTR lpCmdLine, int nCmdShow) {
    CoInitialize(NULL);

    // Get current installation directory
    WCHAR currentDir[MAX_PATH];
    GetModuleFileNameW(NULL, currentDir, MAX_PATH);
    WCHAR* lastSlash = wcsrchr(currentDir, L'\\');
    if (lastSlash) *lastSlash = L'\0';
    SetCurrentDirectoryW(currentDir);

    // Paths
    WCHAR exePath[MAX_PATH];
    _snwprintf(exePath, MAX_PATH, L"%s\\OmniStream.exe", currentDir);

    WCHAR iconPath[MAX_PATH];
    _snwprintf(iconPath, MAX_PATH, L"%s\\static\\icon.ico", currentDir);

    WCHAR desktopPath[MAX_PATH];
    WCHAR desktopLnk[MAX_PATH];
    if (SUCCEEDED(SHGetFolderPathW(NULL, CSIDL_DESKTOPDIRECTORY, NULL, 0, desktopPath))) {
        _snwprintf(desktopLnk, MAX_PATH, L"%s\\OmniStream.lnk", desktopPath);
    } else {
        desktopLnk[0] = L'\0';
    }

    WCHAR programsPath[MAX_PATH];
    WCHAR programsLnk[MAX_PATH];
    if (SUCCEEDED(SHGetFolderPathW(NULL, CSIDL_PROGRAMS, NULL, 0, programsPath))) {
        _snwprintf(programsLnk, MAX_PATH, L"%s\\OmniStream.lnk", programsPath);
    } else {
        programsLnk[0] = L'\0';
    }

    const WCHAR* regUninstallKey = L"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\OmniStream";

    // ==========================================
    // UNINSTALL MODE: invoked by Windows Settings / Control Panel
    // ==========================================
    if (strstr(lpCmdLine, "/uninstall") || strstr(lpCmdLine, "-uninstall")) {
        int confirm = MessageBoxW(
            NULL,
            L"Are you sure you want to uninstall OmniStream from your computer?\n\n"
            L"Your downloaded files will remain intact in your downloads folder.",
            L"Uninstall OmniStream",
            MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND
        );

        if (confirm != IDYES) {
            CoUninitialize();
            return 0;
        }

        // 1. Remove Desktop shortcut
        if (desktopLnk[0] != L'\0') {
            DeleteFileW(desktopLnk);
        }

        // 2. Remove Start Menu shortcut
        if (programsLnk[0] != L'\0') {
            DeleteFileW(programsLnk);
        }

        // 3. Remove Windows Add/Remove Programs Registry entry
        RegDeleteKeyW(HKEY_CURRENT_USER, regUninstallKey);

        MessageBoxW(
            NULL,
            L"OmniStream was successfully uninstalled from your computer.",
            L"OmniStream Uninstall",
            MB_OK | MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND
        );

        CoUninitialize();
        return 0;
    }

    // ==========================================
    // INSTALL / SETUP MODE
    // ==========================================
    int prompt = MessageBoxW(
        NULL,
        L"Welcome to OmniStream Setup!\n\n"
        L"This wizard will register OmniStream in Windows (Add/Remove Programs) and "
        L"create Desktop and Start Menu shortcuts.\n\n"
        L"Do you want to proceed with installation?",
        L"OmniStream Setup",
        MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND
    );

    if (prompt != IDYES) {
        CoUninitialize();
        return 0;
    }

    // 1. Create Desktop shortcut
    if (desktopLnk[0] != L'\0') {
        CreateShortcut(exePath, desktopLnk, L"OmniStream Media Workspace", iconPath, currentDir);
    }

    // 2. Create Start Menu Programs shortcut
    if (programsLnk[0] != L'\0') {
        CreateShortcut(exePath, programsLnk, L"OmniStream Media Workspace", iconPath, currentDir);
    }

    // 3. Register OmniStream in Windows Add/Remove Programs (Installed Apps)
    HKEY hKey;
    if (RegCreateKeyExW(HKEY_CURRENT_USER, regUninstallKey, 0, NULL, 0, KEY_WRITE, NULL, &hKey, NULL) == ERROR_SUCCESS) {
        WCHAR dispName[] = L"OmniStream";
        WCHAR dispVer[] = L"2.1.0";
        WCHAR pub[] = L"OmniStream Team";
        WCHAR uninstCmd[MAX_PATH * 2];
        _snwprintf(uninstCmd, sizeof(uninstCmd)/sizeof(WCHAR), L"\"%s\\OmniStream_Setup.exe\" /uninstall", currentDir);
        
        DWORD dwordOne = 1;
        DWORD estSize = 125000; // ~125 MB

        RegSetValueExW(hKey, L"DisplayName", 0, REG_SZ, (const BYTE*)dispName, (wcslen(dispName) + 1) * sizeof(WCHAR));
        RegSetValueExW(hKey, L"DisplayVersion", 0, REG_SZ, (const BYTE*)dispVer, (wcslen(dispVer) + 1) * sizeof(WCHAR));
        RegSetValueExW(hKey, L"Publisher", 0, REG_SZ, (const BYTE*)pub, (wcslen(pub) + 1) * sizeof(WCHAR));
        RegSetValueExW(hKey, L"DisplayIcon", 0, REG_SZ, (const BYTE*)iconPath, (wcslen(iconPath) + 1) * sizeof(WCHAR));
        RegSetValueExW(hKey, L"InstallLocation", 0, REG_SZ, (const BYTE*)currentDir, (wcslen(currentDir) + 1) * sizeof(WCHAR));
        RegSetValueExW(hKey, L"UninstallString", 0, REG_SZ, (const BYTE*)uninstCmd, (wcslen(uninstCmd) + 1) * sizeof(WCHAR));
        RegSetValueExW(hKey, L"QuietUninstallString", 0, REG_SZ, (const BYTE*)uninstCmd, (wcslen(uninstCmd) + 1) * sizeof(WCHAR));
        RegSetValueExW(hKey, L"NoModify", 0, REG_DWORD, (const BYTE*)&dwordOne, sizeof(DWORD));
        RegSetValueExW(hKey, L"NoRepair", 0, REG_DWORD, (const BYTE*)&dwordOne, sizeof(DWORD));
        RegSetValueExW(hKey, L"EstimatedSize", 0, REG_DWORD, (const BYTE*)&estSize, sizeof(DWORD));

        RegCloseKey(hKey);
    }

    // Success dialog with option to launch
    int launchPrompt = MessageBoxW(
        NULL,
        L"OmniStream has been successfully installed!\n\n"
        L"• Registered in Windows Installed Apps (Add/Remove Programs)\n"
        L"• Shortcuts created on Desktop and Start Menu\n\n"
        L"Would you like to launch OmniStream now?",
        L"OmniStream Setup Completed",
        MB_YESNO | MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND
    );

    if (launchPrompt == IDYES) {
        ShellExecuteW(NULL, L"open", exePath, NULL, currentDir, SW_SHOWNORMAL);
    }

    CoUninitialize();
    return 0;
}
