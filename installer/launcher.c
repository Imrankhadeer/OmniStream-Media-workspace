#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <shlobj.h>
#include <stdio.h>
#include <stdlib.h>

typedef HRESULT (WINAPI *SetAppIDFunc)(PCWSTR);

int WINAPI WinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance, LPSTR lpCmdLine, int nCmdShow) {
    // Set explicit AppUserModelID so Windows groups process under OmniStream
    HMODULE hShell = LoadLibraryA("shell32.dll");
    if (hShell) {
        SetAppIDFunc setAppId = (SetAppIDFunc)GetProcAddress(hShell, "SetCurrentProcessExplicitAppUserModelID");
        if (setAppId) {
            setAppId(L"OmniStream.Workstation.App.2.1");
        }
        FreeLibrary(hShell);
    }

    char exePath[MAX_PATH];
    GetModuleFileNameA(NULL, exePath, MAX_PATH);
    char *lastBackslash = strrchr(exePath, '\\');
    if (lastBackslash != NULL) {
        *lastBackslash = '\0';
    }
    SetCurrentDirectoryA(exePath);

    // Prefer pythonw.exe so Windows doesn't create any unskinned Python console window
    char pythonPath[MAX_PATH];
    snprintf(pythonPath, MAX_PATH, "%s\\.venv\\Scripts\\pythonw.exe", exePath);
    
    DWORD fileAttr = GetFileAttributesA(pythonPath);
    if (fileAttr == INVALID_FILE_ATTRIBUTES) {
        // Fallback to python.exe in .venv
        snprintf(pythonPath, MAX_PATH, "%s\\.venv\\Scripts\\python.exe", exePath);
        fileAttr = GetFileAttributesA(pythonPath);
        if (fileAttr == INVALID_FILE_ATTRIBUTES) {
            strcpy(pythonPath, "pythonw.exe");
        }
    }

    char cmdLine[MAX_PATH * 2];
    snprintf(cmdLine, sizeof(cmdLine), "\"%s\" \"%s\\tray_app.py\"", pythonPath, exePath);

    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;
    ZeroMemory(&pi, sizeof(pi));

    if (CreateProcessA(NULL, cmdLine, NULL, NULL, FALSE, 0, NULL, exePath, &si, &pi)) {
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
        return 0;
    } else {
        MessageBoxA(NULL, 
            "Could not launch OmniStream runtime.\nPlease ensure Python or the .venv virtual environment is installed.", 
            "OmniStream Launcher", 
            MB_ICONERROR | MB_OK);
        return 1;
    }
}
