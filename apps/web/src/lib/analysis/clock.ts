/**
 * The single playback clock. Video, canvas overlay, 3D twin and charts all read it, so they can't
 * drift apart. It is a plain mutable object on purpose: per-frame consumers subscribe and write to
 * canvases or the scene directly, and React only hears about it through useClockTick at a low rate.
 *
 * With a video attached, the video is the master, through requestVideoFrameCallback. With no video
 * (footage isn't distributed in the repository), the clock runs on requestAnimationFrame.
 */

export type ClockListener = (frame: number) => void

type FrameVideo = HTMLVideoElement & {
  requestVideoFrameCallback?: (callback: () => void) => number
  cancelVideoFrameCallback?: (handle: number) => void
}

export class PlaybackClock {
  frame = 0
  playing = false
  rate = 1
  fps = 10
  frameCount = 1

  private listeners = new Set<ClockListener>()
  private video: FrameVideo | null = null
  private videoHandle: number | null = null
  private animationHandle: number | null = null
  private lastTick = 0

  configure(fps: number, frameCount: number): void {
    this.fps = fps
    this.frameCount = Math.max(1, frameCount)
    this.seek(Math.min(this.frame, this.frameCount - 1))
  }

  subscribe(listener: ClockListener): () => void {
    this.listeners.add(listener)
    listener(this.frame)
    return () => {
      this.listeners.delete(listener)
    }
  }

  get(): number {
    return this.frame
  }

  seconds(): number {
    return this.frame / this.fps
  }

  seek(frame: number): void {
    this.frame = Math.max(0, Math.min(frame, this.frameCount - 1))
    if (this.video) this.video.currentTime = this.frame / this.fps
    this.emit()
  }

  play(): void {
    if (this.playing) return
    this.playing = true
    if (this.video) {
      void this.video.play()
      this.watchVideo()
    } else {
      this.lastTick = performance.now()
      this.animationHandle = requestAnimationFrame(this.onAnimationFrame)
    }
    this.emit()
  }

  pause(): void {
    this.playing = false
    this.video?.pause()
    if (this.animationHandle !== null) cancelAnimationFrame(this.animationHandle)
    this.animationHandle = null
    this.emit()
  }

  toggle(): void {
    if (this.playing) this.pause()
    else this.play()
  }

  setRate(rate: number): void {
    this.rate = rate
    if (this.video) this.video.playbackRate = rate
    this.emit()
  }

  /** Make a video the master, or pass null to fall back to the animation clock. */
  attachVideo(video: HTMLVideoElement | null): void {
    if (this.videoHandle !== null) this.video?.cancelVideoFrameCallback?.(this.videoHandle)
    this.videoHandle = null
    const wasPlaying = this.playing
    if (wasPlaying) this.pause()
    this.video = video as FrameVideo | null
    if (this.video) {
      this.video.playbackRate = this.rate
      this.video.currentTime = this.frame / this.fps
    }
    if (wasPlaying) this.play()
  }

  private watchVideo(): void {
    const video = this.video
    if (!video?.requestVideoFrameCallback) return
    this.videoHandle = video.requestVideoFrameCallback(() => {
      this.frame = Math.min(video.currentTime * this.fps, this.frameCount - 1)
      this.emit()
      if (!video.paused && !video.ended) this.watchVideo()
      else if (video.ended) this.pause()
    })
  }

  private onAnimationFrame = (now: number): void => {
    if (!this.playing) return
    const elapsed = (now - this.lastTick) / 1000
    this.lastTick = now
    this.frame += elapsed * this.fps * this.rate
    if (this.frame >= this.frameCount - 1) this.frame = 0
    this.emit()
    this.animationHandle = requestAnimationFrame(this.onAnimationFrame)
  }

  private emit(): void {
    for (const listener of this.listeners) listener(this.frame)
  }
}

export const clock = new PlaybackClock()
