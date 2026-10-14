class SceneClock:
    """シーンごとの録音時間。録音中だけ数え、一時停止中や録音前は止まる"""

    def __init__(self):
        self.scenes = []  # [シーン名, それまでの秒数, 数え始めた時刻 (止まっていれば None)]

    def add(self, name, now, recording):
        self.pause(now)
        self.scenes.append([name, 0.0, now if recording else None])

    def start(self, now):
        if self.scenes and self.scenes[-1][2] is None:
            self.scenes[-1][2] = now

    def pause(self, now):
        if self.scenes and self.scenes[-1][2] is not None:
            self.scenes[-1][1] += now - self.scenes[-1][2]
            self.scenes[-1][2] = None

    def elapsed(self, index, now):
        _, acc, since = self.scenes[index]
        return acc + (now - since if since is not None else 0.0)

    def total(self, now):
        return sum(self.elapsed(i, now) for i in range(len(self.scenes)))
