"""Deep Q-Network scaffold (PyTorch). Not trained in production yet — this defines the model,
replay buffer and one training step so a research job can be pointed at historical snapshots.

    from app.quant.rl.dqn import DQNAgent
    agent = DQNAgent(); agent.train(episodes, n_epochs=50)
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass

import numpy as np

from app.quant.rl.env import N_ACTIONS, STATE_DIM, EpisodeData, SlabSpreadEnv

try:  # torch is an optional extra; the API image does not ship it.
    import torch
    from torch import nn
    TORCH_AVAILABLE = True
except ImportError:  # pragma: no cover
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]
    TORCH_AVAILABLE = False


@dataclass
class DQNConfig:
    hidden: int = 128
    gamma: float = 0.99
    lr: float = 1e-3
    batch_size: int = 128
    buffer_size: int = 100_000
    eps_start: float = 1.0
    eps_end: float = 0.05
    eps_decay_steps: int = 20_000
    target_sync_every: int = 500
    seed: int = 0


class ReplayBuffer:
    def __init__(self, capacity: int):
        self.buf: deque = deque(maxlen=capacity)

    def push(self, s, a, r, s2, done) -> None:
        self.buf.append((s, a, r, s2, done))

    def sample(self, n: int):
        batch = random.sample(self.buf, n)
        s, a, r, s2, d = map(np.array, zip(*batch, strict=True))
        return s, a, r, s2, d

    def __len__(self) -> int:
        return len(self.buf)


if TORCH_AVAILABLE:

    class QNetwork(nn.Module):
        def __init__(self, state_dim: int = STATE_DIM, n_actions: int = N_ACTIONS, hidden: int = 128):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(state_dim, hidden), nn.ReLU(),
                nn.Linear(hidden, hidden), nn.ReLU(),
                nn.Linear(hidden, n_actions),
            )

        def forward(self, x):  # type: ignore[override]
            return self.net(x)


class DQNAgent:
    def __init__(self, cfg: DQNConfig | None = None):
        if not TORCH_AVAILABLE:
            raise RuntimeError("PyTorch not installed: pip install 'pokequant-api[rl]'")
        self.cfg = cfg or DQNConfig()
        torch.manual_seed(self.cfg.seed)
        random.seed(self.cfg.seed)
        self.q = QNetwork(hidden=self.cfg.hidden)
        self.q_target = QNetwork(hidden=self.cfg.hidden)
        self.q_target.load_state_dict(self.q.state_dict())
        self.opt = torch.optim.Adam(self.q.parameters(), lr=self.cfg.lr)
        self.buffer = ReplayBuffer(self.cfg.buffer_size)
        self.steps = 0

    def epsilon(self) -> float:
        c = self.cfg
        return max(c.eps_end, c.eps_start - (c.eps_start - c.eps_end) * self.steps / c.eps_decay_steps)

    def act(self, state: np.ndarray, greedy: bool = False) -> int:
        if not greedy and random.random() < self.epsilon():
            return random.randrange(N_ACTIONS)
        with torch.no_grad():
            return int(self.q(torch.as_tensor(state).unsqueeze(0)).argmax(dim=1).item())

    def train_step(self) -> float | None:
        if len(self.buffer) < self.cfg.batch_size:
            return None
        s, a, r, s2, d = self.buffer.sample(self.cfg.batch_size)
        s, s2 = torch.as_tensor(s), torch.as_tensor(s2)
        a = torch.as_tensor(a, dtype=torch.long)
        r = torch.as_tensor(r, dtype=torch.float32)
        d = torch.as_tensor(d, dtype=torch.float32)
        q_sa = self.q(s).gather(1, a.unsqueeze(1)).squeeze(1)
        with torch.no_grad():
            # Double-DQN target: online net picks the action, target net evaluates it.
            a2 = self.q(s2).argmax(dim=1, keepdim=True)
            q_next = self.q_target(s2).gather(1, a2).squeeze(1)
            target = r + self.cfg.gamma * (1 - d) * q_next
        loss = nn.functional.smooth_l1_loss(q_sa, target)
        self.opt.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(self.q.parameters(), 10.0)
        self.opt.step()
        self.steps += 1
        if self.steps % self.cfg.target_sync_every == 0:
            self.q_target.load_state_dict(self.q.state_dict())
        return float(loss.item())

    def train(self, episodes: list[EpisodeData], n_epochs: int = 10) -> list[float]:
        history: list[float] = []
        for _ in range(n_epochs):
            for ep in episodes:
                env = SlabSpreadEnv(ep)
                s = env.reset()
                done = False
                while not done:
                    a = self.act(s)
                    s2, r, done, _ = env.step(a)
                    self.buffer.push(s, a, r, s2, float(done))
                    loss = self.train_step()
                    if loss is not None:
                        history.append(loss)
                    s = s2
        return history

    def save(self, path: str) -> None:
        torch.save({"q": self.q.state_dict(), "cfg": self.cfg.__dict__}, path)
