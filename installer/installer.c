#define WIN32_LEAN_AND_MEAN
#define COBJMACROS
#include <windows.h>
#include <shlobj.h>
#include <objbase.h>
#include <stdio.h>
#include <stdlib.h>

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

    // Target executable path
    WCHAR exePath[MAX_PATH];
    _snwprintf(exePath, MAX_PATH, L"%s\\OmniStream.exe", currentDir);

    // Icon path
    WCHAR iconPath[MAX_PATH];
    _snwprintf(iconPath, MAX_PATH, L"%s\\static\\icon.ico", currentDir);

    // Welcome prompt
    int prompt = MessageBoxW(
        NULL,
        L"Welcome to OmniStream Setup!\n\n"
        L"This wizard will configure OmniStream on your computer and create "
        L"Desktop and Start Menu shortcuts.\n\n"
        L"Do you want to continue?",
        L"OmniStream Setup",
        MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND
    );

    if (prompt != IDYES) {
        CoUninitialize();
        return 0;
    }

    // 1. Create Desktop shortcut
    WCHAR desktopPath[MAX_PATH];
    WCHAR desktopLnk[MAX_PATH];
    if (SUCCEEDED(SHGetFolderPathW(NULL, CSIDL_DESKTOPDIRECTORY, NULL, 0, desktopPath))) {
        _snwprintf(desktopLnk, MAX_PATH, L"%s\\OmniStream.lnk", desktopPath);
        CreateShortcut(exePath, desktopLnk, L"OmniStream Media Workspace", iconPath, currentDir);
    }

    // 2. Create Start Menu Programs shortcut
    WCHAR programsPath[MAX_PATH];
    WCHAR programsLnk[MAX_PATH];
    if (SUCCEEDED(SHGetFolderPathW(NULL, CSIDL_PROGRAMS, NULL, 0, programsPath))) {
        _snwprintf(programsLnk, MAX_PATH, L"%s\\OmniStream.lnk", programsPath);
        CreateShortcut(exePath, programsLnk, L"OmniStream Media Workspace", iconPath, currentDir);
    }

    // Success dialog with option to launch
    int launchPrompt = MessageBoxW(
        NULL,
        L"Installation successful!\n\n"
        L"Shortcuts have been created on your Desktop and Start Menu.\n\n"
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
