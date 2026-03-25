# pickpix图片挑选工具

环境配置

pip install -r requirements.txt

代码运行

```
python pickpix.py
```

pickpix.exe为编译好可直接运行的发行版

工具内选择输入目录的时候文件夹内文件构成需要如下

```
source_folder
|-method1
| |-Color.0001.exr(格式可为exr或png，序列下标可以从任意值开始，长度不限死4位数)
| |-……
|-method2
| |-Color.0001.exr
| |-……
|-……
```

其余使用方法参考tutorial.mp4