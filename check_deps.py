# -*- coding: utf-8 -*-
"""Check sub-mod dependency declarations."""
import re, os

MODS = [r'D:\heart of iron\SW00383\langou123\hoi4\mod\2438003901',
        r'D:\heart of iron\SW00383\langou123\hoi4\mod\2243912940',
        r'D:\heart of iron\SW00383\langou123\hoi4\mod\3256452254',
        r'D:\heart of iron\SW00383\langou123\hoi4\mod\2980739000',
        r'D:\heart of iron\SW00383\langou123\hoi4\mod\2989705802']
for m in MODS:
    desc = os.path.join(m, 'descriptor.mod')
    if not os.path.exists(desc):
        print(os.path.basename(m), '无 descriptor')
        continue
    txt = open(desc, encoding='utf-8', errors='ignore').read()
    name = re.search(r'name\s*=\s*"([^"]+)"', txt)
    deps = re.findall(r'dependencies\s*=\s*\{(.*?)\}', txt, re.S)
    dep_names = re.findall(r'"([^"]+)"', deps[0]) if deps else []
    print('%-12s %-55s 依赖: %s' % (os.path.basename(m), name.group(1) if name else '?', dep_names))
