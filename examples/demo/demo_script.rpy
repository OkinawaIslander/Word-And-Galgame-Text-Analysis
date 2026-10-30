define s = Character("诗音")
define y = Character("悠人")

label start:
    scene bg station
    s "还记得我们的约定吗？"
    y "当然记得。"
    "雨声淹没了沉默。"
    s "明天，我还在这里等你。"
    menu:
        "答应她":
            y "我会来的。"
        "沉默":
            "我什么也没说。"
