import ctypes
from ctypes import wintypes
import customtkinter as ctk

GetAncestor = ctypes.windll.user32.GetAncestor
GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
GetAncestor.restype = wintypes.HWND
GA_ROOT = 2

root = ctk.CTk()

def test():
    dialog = ctk.CTkToplevel(root)
    dialog.title('Test')
    dialog.geometry('300x200')
    dialog.update_idletasks()

    tk_id = dialog.winfo_id()
    root_hwnd = GetAncestor(tk_id, GA_ROOT)
    print(f'Tk ID: {tk_id}, Root HWND: {root_hwnd}')

    for attr in [20, 19, 34]:
        r = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            root_hwnd, attr, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
        )
        print(f'DwmSetWindowAttribute({attr}) = {r}')

    dialog.after(3000, lambda: (dialog.destroy(), root.destroy()))

root.after(100, test)
root.mainloop()
print("Done")
