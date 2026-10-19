"""中文数字规范化（ITN）：把识别结果里的汉字数字换成阿拉伯数字，如 一百二十次 → 120次、下午三点 → 下午3点。

宁可少转、不可错转：一起、十分重要、万一、三五天、第三章、三个方法 这类保持原样。
规则按顺序执行；前面转过的部分已是阿拉伯数字，后面的规则不会再碰。
"""
import re

DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10**4, "亿": 10**8}
N = "零〇一二三四五六七八九十百千万亿两"  # 能组成数字的字
D = "零〇一二三四五六七八九"  # 逐位读的数字（不含「两」）
PERIOD = "上午|下午|晚上|早上|早晨|凌晨|中午|傍晚|夜里|半夜|今晚|明晚|昨晚|明早|今早"
MEASURE = "秒|分钟|个小时|小时|天|星期|个月|年|岁|度|块|元|米|厘米|毫米|公里|斤|公斤|克|吨|升|毫升|倍|楼|层|级"
APPROX = {"一二", "二三", "三四", "四五", "五六", "六七", "七八", "八九", "三五"}  # 约数：三五天、五六个
IDIOMS = re.compile("(十万火急|十万八千里|一五一十|千千万万|万万|一千零一夜|三七二十一|二百五|三天两头|三天三夜|三十而立|十八般"
                    "|九天揽月|高八度|七七八八|九一八|十二分(?=的)|十一(?=假|放假|长假|黄金周))")  # 固定说法，整段不动


def cn_int(s):
    """一百零五 → 105，十五 → 15，一万二 → 12000（口语省略了末位单位）"""
    total = section = num = 0
    unit = 1
    for c in s:
        if c in DIGITS:
            num = DIGITS[c]
        elif c in "万亿":
            total += (section + num) * UNITS[c]
            section = num = 0
            unit = UNITS[c]
        else:
            section += (num or 1) * UNITS[c]
            num = 0
            unit = UNITS[c]
    if num and len(s) > 1 and s[-2] in "百千万亿":  # 一百五 → 150
        num *= unit // 10
    return total + section + num


def render(s):
    """汉字数字串 → 阿拉伯数字串"""
    if "点" in s:  # 小数：三点一四 → 3.14
        whole, frac = s.split("点", 1)
        return f"{render(whole)}.{''.join(str(DIGITS[c]) for c in frac)}"
    if all(c in D for c in s):  # 逐位读：一三八…，零零 → 00 保留前导零
        return "".join(str(DIGITS[c]) for c in s)
    if len(s) > 1 and s[-1] in "万亿" and not set(s[:-1]) & set("万亿"):
        return f"{cn_int(s[:-1])}{s[-1]}"  # 两万 → 2万，三千万 → 3000万
    return str(cn_int(s))


def _seq(m):
    s = m[0]
    if len(s) == 2 and not set(s) & set("零〇"):
        if m.string[m.end():m.end() + 1] not in ("后", "年") or s in APPROX:
            return s  # 五一放假、四六级、三五天：节日、专名、约数；九五后、九八年才转
    return render(s)


def _general(m):
    s = m[0]
    if s[0] in "百千万亿" or not set(s) & set(UNITS):
        return s  # 千万别、万一；两三、一两 这类约数
    return render(s)


def _time(m):
    return render(m[1]) + "点" + (render(m[2]) + "分" if m[2] else "")


def _range(m):
    if len(m[1]) == 1 and len(m[3]) == 1:
        return m[0]  # 三到五个
    return f"{render(m[1])}{m[2]}{render(m[3])}"


_RULES = [(re.compile(p), r) for p, r in [
    (f"百分之([{N}]+(?:点[{D}]+)?)", lambda m: render(m[1]) + "%"),
    (f"(?<![{N}])([{N}]+)分之([{N}]+(?:点[{D}]+)?)", lambda m: f"{render(m[2])}/{render(m[1])}"),
    (f"(?<={PERIOD})([{N}]{{1,3}})点(?:([{N}]{{1,3}})分)?", _time),  # 下午三点、晚上十点半
    (f"(?<![{N}])([{N}]{{1,3}})点(?:([{N}]{{1,3}})分|(?=[钟半整]))", _time),  # 三点钟、两点十五分
    (f"(?<![{N}几])([一二三四五六七八九十]{{1,2}})(?=月)", lambda m: render(m[1])),  # 十月
    (f"(?<![{N}几])([{N}]{{1,3}})(?=号)", lambda m: render(m[1])),  # 十九号、一号线
    (f"(?<=月)([{N}]{{1,3}})(?=日)", lambda m: render(m[1])),
    (f"(?<![{N}])[{N}]+(?:、[{N}]+)+(?![{N}])", lambda m: "、".join(map(render, m[0].split("、")))),  # 一、二、三
    (f"(?<![{N}])[{N}](?:，[{N}]){{2,}}(?![{N}])", lambda m: "，".join(map(render, m[0].split("，")))),
    (f"(?<![{N}])([{N}]+)比([{N}]+)(?![{N}])", lambda m: f"{render(m[1])}比{render(m[2])}"),
    (f"(?<![{N}几第])([{N}]+)(到|至)([{N}]+)(?![{N}几])", _range),
    (f"(?<![{N}几])([{N}]+点[{D}]+)(?![{N}点])", lambda m: render(m[1])),  # 三点一四、零点五
    (f"(?<![{N}几])[{D}]{{2,}}(?![{N}几])", _seq),  # 手机号、二零二六年、九五后
    (f"(?<![{N}几第])[{N}]{{2,}}(?![{N}几])", _general),  # 一百二十、三千万、二十一
    (f"(?<![{N}几第])([二三四五六七八九十两])(?={MEASURE})", lambda m: render(m[1])),  # 五秒、三天、十分钟
]]


def normalize(text):
    parts = IDIOMS.split(text)  # 奇数下标是成语，原样保留
    for i in range(0, len(parts), 2):
        for pattern, repl in _RULES:
            parts[i] = pattern.sub(repl, parts[i])
    return "".join(parts)
