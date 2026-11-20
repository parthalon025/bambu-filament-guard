"""The estimators learning uses (guard_learn), as plain functions: numbers in, numbers out, no files, no printer.

They are kept apart from guard_learn because they are general arithmetic with tests of their own (each against a
figure worked by hand or a known property), while guard_learn decides what a printer's records mean. Pure Python: the
packaged guard carries every dependency, and none of this needs more than the math module.

  quantile        a sample quantile (linear between order statistics)
  conformal_bound one-sided split-conformal upper bound of scores (Shafer and Vovk 2008)
  shrinkage       how far a group's mean is drawn to the mean of all groups (method of moments)
  fieller         the interval of a ratio of two noisy sums (Fieller 1954; Franz 2007), and ratio_interval, its use for a
                  ratio whose denominator carries no noise of its own
  prediction_bound the one-sided bound for one more value from a Normal sample (with pooled_spread, t_quantile)
  km_upper        an upper quantile of values some of which are known only to lie below a bound (Kaplan-Meier on the
                  flipped scale, the usual way to treat left-censoring)
  sign_p          one-sided sign test
  run_lengths     Bayesian online change-point detection (Adams and MacKay 2007), Normal data of unknown mean and
                  variance, constant hazard"""
import math
from statistics import NormalDist


def quantile(xs, p):
    """The p-quantile of xs, interpolated between order statistics (as most statistics packages do by default)."""
    s = sorted(xs)
    if not s: return None
    h = (len(s) - 1) * p
    lo = math.floor(h)
    return s[lo] + (s[min(lo + 1, len(s) - 1)] - s[lo]) * (h - lo)

def conformal_bound(scores, alpha):
    """The ceil((n+1)(1-alpha))-th smallest score: a new score exchangeable with these lies at or below it with
    probability at least 1 - alpha. None when there are too few scores for that (n < 1/alpha - 1)."""
    n = len(scores)
    k = math.ceil((n + 1) * (1 - alpha))
    return sorted(scores)[k - 1] if 1 <= k <= n else None

def weighted_mean(values, weights):
    total = sum(weights)
    return sum(v * w for v, w in zip(values, weights)) / total if total > 0 else None

def group_stats(values, weights):
    """(mean, effective count, variance within) of weighted values. The effective count is Kish's (sum w)^2 / sum w^2;
    the variance is None below two effective values."""
    m = weighted_mean(values, weights)
    if m is None: return None, 0.0, None
    sw, sw2 = sum(weights), sum(w * w for w in weights)
    n = sw * sw / sw2
    if n < 1.5: return m, n, None
    return m, n, sum(w * (v - m) ** 2 for v, w in zip(values, weights)) / sw * n / (n - 1)

def shrinkage(groups, least=1.0):
    """k in (n x own + k x all) / (n + k), from groups [(mean, effective count, variance within or None)]: the variance
    within groups over the variance between their true means, both by the method of moments. None: the groups cannot
    be told apart (no spread between them beyond what the spread within explains, or nothing to measure it by), so
    each takes the mean of all. Never below `least`: one record never outweighs everything else."""
    inner = [(v, n) for _, n, v in groups if v is not None and n > 1]
    if len(groups) < 2 or not inner: return None
    within = sum(v * (n - 1) for v, n in inner) / sum(n - 1 for _, n in inner)
    means = [m for m, _, _ in groups]
    centre = sum(means) / len(means)
    between = sum((m - centre) ** 2 for m in means) / (len(means) - 1) - sum(within / n for _, n, _ in groups) / len(groups)
    if between <= 0: return None
    return max(least, within / between)

def t_quantile(p, df):
    """The p-quantile (p above 0.5) of Student's t with df degrees of freedom: exact for 1 and 2, the Cornish-Fisher
    series beyond (within 0.01 of the tables from 3 on)."""
    if df <= 1: return math.tan(math.pi * (p - 0.5))
    if df == 2: return (2 * p - 1) / (2 * p * (1 - p)) ** 0.5
    z, v = NormalDist().inv_cdf(p), float(df)
    return (z + (z ** 3 + z) / (4 * v) + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * v * v)
            + (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / (384 * v ** 3))

def pooled_spread(groups):
    """(standard deviation, degrees of freedom) of values about their own group's mean, pooled over the groups; (None, 0)
    when no group has two values."""
    df = sum(len(g) - 1 for g in groups if g)
    if df < 1: return None, 0
    ss = sum((v - sum(g) / len(g)) ** 2 for g in groups if g for v in g)
    return (ss / df) ** 0.5, df

def prediction_bound(values, spread, df, p):
    """The one-sided p bound for one more value like these (Normal, spread estimated with df degrees of freedom):
    mean + t(p, df) x spread x sqrt(1 + 1/n)."""
    n = len(values)
    return sum(values) / n + t_quantile(p, df) * spread * (1 + 1 / n) ** 0.5

def fieller(x, y, vxx, vyy, vxy, t):
    """(low, high): the interval of y / x by Fieller's theorem, the values r for which (y - r x)^2 <= t^2 Var(y - r x)
    = t^2 (vyy - 2 r vxy + r^2 vxx). None when x is not clearly away from 0 (the interval is then not bounded)."""
    a, b, c = x * x - t * t * vxx, x * y - t * t * vxy, y * y - t * t * vyy
    disc = b * b - a * c
    if a <= 0 or disc < 0: return None
    return (b - disc ** 0.5) / a, (b + disc ** 0.5) / a

def ratio_interval(pairs):
    """The 90 % interval of sum(y) / sum(x) over pairs (x, y) where x carries no noise of its own (the slicer's grams)
    and y does (what a reading fell): Fieller with Var(x) = 0, Var(sum y) from the pairs' residuals about the ratio.
    None with fewer than two pairs."""
    n = len(pairs)
    if n < 2: return None
    x, y = sum(p for p, _ in pairs), sum(q for _, q in pairs)
    if x <= 0: return None
    r = y / x
    return fieller(x, y, 0.0, n / (n - 1) * sum((q - r * p) ** 2 for p, q in pairs), 0.0, t_quantile(0.95, n - 1))

def km_upper(observations, alpha):
    """The upper alpha-quantile of a value seen exactly in some cases and only as "below v" in others: observations
    [(v, exact)]. Flipped (b = -v), "below v" becomes "above -v", a right-censored time, and the Kaplan-Meier product
    gives the distribution; returned is the largest exact v whose share of the distribution at or above it reaches
    alpha. None when the exact values do not carry that share (what lies above is not resolved)."""
    flipped = sorted(((-v, exact) for v, exact in observations), key=lambda o: (o[0], not o[1]))   # at a tie the exact value comes first, as Kaplan-Meier has it
    at_risk, surviving, i = len(flipped), 1.0, 0
    while i < len(flipped):
        b = flipped[i][0]
        here = [e for x, e in flipped[i:] if x == b]
        events = sum(1 for e in here if e)
        if events:
            surviving *= 1 - events / at_risk
            if 1 - surviving >= alpha - 1e-12: return -b
        at_risk -= len(here); i += len(here)
    return None

def sign_p(worse, pairs):
    """P(at least `worse` of `pairs` fall one way) when either way is as likely: the one-sided sign test."""
    return sum(math.comb(pairs, i) for i in range(worse, pairs + 1)) / 2 ** pairs if pairs else 1.0

def _student(x, mu, kappa, alpha, beta):
    """The predictive density of the Normal model with a Normal-Gamma prior: Student's t, 2 alpha degrees of freedom."""
    nu, scale2 = 2 * alpha, beta * (kappa + 1) / (alpha * kappa)
    return math.exp(math.lgamma((nu + 1) / 2) - math.lgamma(nu / 2) - 0.5 * math.log(nu * math.pi * scale2)
                    - (nu + 1) / 2 * math.log1p((x - mu) ** 2 / (nu * scale2)))

def run_lengths(xs, hazard, prior, outlier=0.0):
    """Bayesian online change-point detection: after each value of xs, the probability of each run length r (r values
    in the run that the latest value belongs to, 1..t), as a list indexed by r (index 0: the run that starts after
    it, which holds `hazard`). prior: (mu, kappa, alpha, beta) of the Normal-Gamma prior each new run starts from.
    outlier: the share of values taken to be odd ones, from the prior's spread whatever the run: without it one odd
    value ends a long run for good (a Normal run cannot hold it), and is read as a change."""
    runs, out = [(1.0, prior)], []                     # (probability, the run's posterior parameters); first: no values yet
    for x in xs:
        odd = outlier * _student(x, *prior)
        dens = [(1 - outlier) * _student(x, *par) + odd for _, par in runs]
        grown = [(p * d * (1 - hazard), par) for (p, par), d in zip(runs, dens)]
        restart = sum(p * d for (p, _), d in zip(runs, dens)) * hazard
        total = sum(p for p, _ in grown) + restart
        if total <= 0 or not math.isfinite(total): grown, restart, total = [(1.0, par) for _, par in runs[:1]], 0.0, 1.0
        nxt = [(restart / total, prior)]
        for p, (mu, kappa, alpha, beta) in grown:
            nxt.append((p / total, ((kappa * mu + x) / (kappa + 1), kappa + 1, alpha + 0.5, beta + kappa * (x - mu) ** 2 / (2 * (kappa + 1)))))
        runs = nxt
        out.append([p for p, _ in runs])
    return out
