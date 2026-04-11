import os

os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"

import tkinter as tk

from pickpix_app.frontend.gui import MultiMethodCropperGUI


def main() -> None:
    root = tk.Tk()
    MultiMethodCropperGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
