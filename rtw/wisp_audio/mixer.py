"""把两路单声道流（本地麦克风与会议/系统音频）混成一路——**增益共享自动混音器**
（Dugan 算法，数十年来广播/会议的标配）。

每路的增益是其*占比*的组合电平份额，因此增益之和恒 ≤ 1，混音是输入的凸组合
——**永不削顶**（输出 ≤ 最响源 ≤ 满量程），无需限幅器。活跃说话人拿走几乎全部
增益（安静一侧的背景渗漏被压制），两位同时说话则共享增益（重叠语音得以保留）。
快攻慢放的包络跟随器平滑移动增益：新说话人一帧内抢到增益，字间增益不泵动。

这优于会议工具惯用的两种朴素方案：`(a + b).clamp(-1, 1)` 求和（响亮重叠平顶削波
失真）与单输出录音器的纯 ducking（直接丢掉安静一侧，丢失重叠）。对下游听者——
ASR 模型或 AI 助手——**两位说话人都清晰可辨**且**信号无削波**才是关键。
"""
from __future__ import annotations

#: 低于此组合电平的场景区视为静音——把（近乎为零的）混音平分，而非拿噪声除以
#: 噪声。
SILENCE = 1e-4

#: 每帧包络跟随器系数：朝更响的峰快速上升（新说话人约一帧内抢到增益，onset 不
#: 被 duck 掉），缓慢下降（字间增益滑降而非泵动）。
ENV_ATTACK = 0.7
ENV_RELEASE = 0.1


def _peak(samples: list[float]) -> float:
    """samples 的峰值绝对幅度（空切片为 0）。"""
    return max((abs(x) for x in samples), default=0.0)


def _follow(level: float, target: float) -> float:
    """把 level 朝 target 追踪，快升慢降（标准包络跟随器）。"""
    coeff = ENV_ATTACK if target > level else ENV_RELEASE
    return level + coeff * (target - level)


def _share_gains(level_a: float, level_b: float) -> tuple[float, float]:
    """两路源电平的增益份额权重：各占总体的比例。它们之和为 1（双方静音时
    平分），故施加后得到无削波的凸混音。"""
    total = level_a + level_b
    if total < SILENCE:
        return (0.5, 0.5)
    return (level_a / total, level_b / total)


class MeetingMixer:
    """两路单声道流的增益共享自动混音器。有状态：它跨帧携带每路源的平滑电平
    用于增益决策，故需按序喂给它连续的帧。"""

    def __init__(self) -> None:
        self.level_primary = 0.0
        self.level_secondary = 0.0

    def mix(self, primary: list[float], secondary: list[float]) -> None:
        """就地把 secondary 混入 primary。二者应等长；较短的 secondary 会让尾部
        primary 样本按自身增益缩放。每路都做电平跟随与增益共享，故较响说话人
        占主导、重叠共享、凸混音永不削顶（最终 clamp 只为保护个别源罕见地因
        采样器过冲超过满量程的情况）。"""
        self.level_primary = _follow(self.level_primary, _peak(primary))
        self.level_secondary = _follow(self.level_secondary, _peak(secondary))

        gain_primary, gain_secondary = _share_gains(
            self.level_primary, self.level_secondary)

        overlap = min(len(primary), len(secondary))
        for i in range(overlap):
            p = primary[i] * gain_primary + secondary[i] * gain_secondary
            primary[i] = max(-1.0, min(1.0, p))
        for i in range(overlap, len(primary)):
            primary[i] = max(-1.0, min(1.0, primary[i] * gain_primary))
