import os

os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"

from pickpix_app.frontend.qt import main


if __name__ == "__main__":
    main()
