# -*- coding: utf-8 -*-
"""search —— 相机/机位连续参数搜索（0.6.0）

两种无第三方依赖的实现：
- cmaes：协方差矩阵自适应进化策略，适合 2~8 维连续参数；
- bayes_opt：GP + Expected Improvement，适合“每次评估很贵”的渲染反馈。

score_fn(x) 由调用方提供（例如渲染低清图→vision_check 评分）。
"""
import math

import numpy as np


def _normal_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _normal_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def cmaes(score_fn, x0, sigma=0.25, bounds=None, popsize=None,
          iters=40, seed=7):
    """最小化 score_fn(x)；bounds=[(lo,hi),...] 可选。返回 (best_x, best_y, trace)。"""
    x0 = np.asarray(x0, dtype=np.float64)
    n = x0.size
    lam = int(popsize or (4 + int(3 * math.log(n))))
    mu = lam // 2
    weights = np.log(mu + 0.5) - np.log(np.arange(1, mu + 1))
    weights /= weights.sum()
    mueff = 1.0 / np.sum(weights ** 2)
    cc = (4 + mueff / n) / (n + 4 + 2 * mueff / n)
    cs = (mueff + 2) / (n + mueff + 5)
    c1 = 2 / ((n + 1.3) ** 2 + mueff)
    cmu = min(1 - c1, 2 * (mueff - 2 + 1 / mueff) / ((n + 2) ** 2 + mueff))
    damps = 1 + 2 * max(0, math.sqrt((mueff - 1) / (n + 1)) - 1) + cs
    pc = np.zeros(n)
    ps = np.zeros(n)
    m = x0.copy()
    B = np.eye(n)
    D = np.ones(n)
    C = np.eye(n)
    chi_n = math.sqrt(n) * (1 - 1 / (4 * n) + 1 / (21 * n * n))
    rng = np.random.default_rng(seed)
    best_x, best_y = x0.copy(), float(score_fn(x0))
    trace = [best_y]
    for _ in range(int(iters)):
        arz = rng.standard_normal((lam, n))
        ary = arz @ (B * D).T
        arx = m + sigma * ary
        if bounds is not None:
            lo = np.array([b[0] for b in bounds])
            hi = np.array([b[1] for b in bounds])
            arx = np.clip(arx, lo, hi)
        vals = np.array([float(score_fn(x)) for x in arx])
        idx = np.argsort(vals)
        if vals[idx[0]] < best_y:
            best_x, best_y = arx[idx[0]].copy(), float(vals[idx[0]])
        trace.append(best_y)
        y_w = ary[idx[:mu]]
        m_old = m.copy()
        m = m + sigma * (weights[:, None] * y_w).sum(axis=0)
        y_mean = (m - m_old) / max(sigma, 1e-12)
        invsqrtC = B @ np.diag(1.0 / np.maximum(D, 1e-12)) @ B.T
        ps = (1 - cs) * ps + math.sqrt(cs * (2 - cs) * mueff) * (invsqrtC @ y_mean)
        hsig = np.linalg.norm(ps) / math.sqrt(1 - (1 - cs) ** (2 * (_ + 1))) / chi_n < 1.4 + 2 / (n + 1)
        pc = (1 - cc) * pc + hsig * math.sqrt(cc * (2 - cc) * mueff) * y_mean
        C = ((1 - c1 - cmu) * C
             + c1 * (np.outer(pc, pc) + (1 - hsig) * cc * (2 - cc) * C)
             + cmu * (y_w.T * weights) @ y_w)
        sigma *= math.exp((cs / damps) * (np.linalg.norm(ps) / chi_n - 1))
        C = (C + C.T) / 2
        try:
            D2, B = np.linalg.eigh(C)
            D = np.sqrt(np.maximum(D2, 1e-12))
        except np.linalg.LinAlgError:
            B, D = np.eye(n), np.ones(n)
    return best_x, best_y, trace


def _gp_fit(X, y, length=0.35, noise=1e-3):
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    d2 = ((X[:, None, :] - X[None, :, :]) ** 2).sum(axis=2)
    K = np.exp(-0.5 * d2 / (length * length)) + noise * np.eye(len(X))
    L = np.linalg.cholesky(K + 1e-10 * np.eye(len(X)))
    alpha = np.linalg.solve(L.T, np.linalg.solve(L, y))
    return X, y, L, alpha, length


def _gp_predict(model, xs):
    X, y, L, alpha, length = model
    d2 = ((xs[:, None, :] - X[None, :, :]) ** 2).sum(axis=2)
    Ks = np.exp(-0.5 * d2 / (length * length))
    mu = Ks @ alpha
    v = np.linalg.solve(L, Ks.T)
    var = np.maximum(1e-9, 1.0 - (v * v).sum(axis=0))
    return mu, np.sqrt(var)


def bayes_opt(score_fn, bounds, n_init=12, iters=20, seed=11):
    """GP + EI 最小化 score_fn；bounds=[(lo,hi),...]。返回 (best_x,best_y,trace)。"""
    lo = np.array([b[0] for b in bounds], float)
    hi = np.array([b[1] for b in bounds], float)
    rng = np.random.default_rng(seed)
    X = lo + (hi - lo) * rng.random((n_init, len(lo)))
    y = np.array([float(score_fn(x)) for x in X])
    trace = [float(y.min())]
    for _ in range(int(iters)):
        model = _gp_fit(X, y)
        cand = lo + (hi - lo) * rng.random((600, len(lo)))
        mu, sd = _gp_predict(model, cand)
        best = float(y.min())
        imp = best - mu - 0.01
        z = imp / np.maximum(sd, 1e-9)
        ei = imp * np.vectorize(_normal_cdf)(z) + sd * np.vectorize(_normal_pdf)(z)
        x = cand[int(np.argmax(ei))]
        val = float(score_fn(x))
        X = np.vstack([X, x])
        y = np.append(y, val)
        trace.append(float(y.min()))
    i = int(np.argmin(y))
    return X[i], float(y[i]), trace
