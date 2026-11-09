import json
import os
import wave
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from dotenv import load_dotenv

from database import init_db, create_project, get_projects, get_project, update_project
from engines.story_engine import generate_story
from engines.voice_engine import generate_voice
from engines.subtitle_engine import generate_subtitles
from engines.visual_engine import create_scene_clip, combine_scene_clips
from engines.storyboard_engine import create_storyboard_image
from engines.composition_engine import create_final_video
from engines.music_manager import get_music_for_style
from engines.storyboard_engine import select_camera_motion

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "local-cineforge-development-key")
init_db()


@app.route("/")
def dashboard():
    projects = get_projects()
    return render_template("dashboard.html", projects=projects, active="dashboard")


@app.route("/projects/new", methods=["GET", "POST"])
def new_project():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        idea = request.form.get("idea", "").strip()
        genre = request.form.get("genre", "Mystery").strip()
        duration = request.form.get("duration", "60").strip()
        if not title or not idea:
            flash("Please provide a project title and creative idea.", "error")
            return render_template("new_project.html", active="new")
        project_id = create_project(title, idea, genre, duration)
        try:
            result = generate_story(idea, genre, int(duration))
            update_project(project_id, status="Script ready", script=result["script"], scenes=result["scenes"], error="")
            flash("Project created and script generated successfully.", "success")
        except Exception as exc:
            update_project(project_id, status="Needs attention", error=str(exc))
            flash("Project was saved, but local AI generation could not run. Check Ollama and try again.", "warning")
        return redirect(url_for("project_detail", project_id=project_id))
    return render_template("new_project.html", active="new")


@app.route("/projects/<int:project_id>")
def project_detail(project_id):
    project = get_project(project_id)
    if not project:
        return "Project not found", 404
    return render_template("project_detail.html", project=project, active="dashboard")


@app.route("/api/health")
def health():
    import requests
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        response = requests.get(base.rstrip("/") + "/api/tags", timeout=3)
        response.raise_for_status()
        models = [m.get("name") for m in response.json().get("models", [])]
        return jsonify({"flask": "online", "ollama": "online", "models": models})
    except Exception as exc:
        return jsonify({"flask": "online", "ollama": "offline", "detail": str(exc)}), 503


@app.route("/projects/<int:project_id>/generate-voice", methods=["POST"])
def generate_project_voice(project_id):
    project = get_project(project_id)
    if not project:
        return "Project not found", 404
    try:
        script = project.get("script", "")
        if not script:
            flash("Generate a script before creating narration.", "error")
            return redirect(url_for("project_detail", project_id=project_id))
        output_dir = Path(app.root_path) / "projects" / str(project_id)
        output_path = output_dir / "narration.wav"
        generate_voice(script, str(output_path))
        update_project(project_id, voice_path=f"projects/{project_id}/narration.wav", error="")
        flash("Voice narration generated successfully!", "success")
    except Exception as exc:
        app.logger.exception("Voice generation failed")
        flash(f"Voice generation failed: {exc}", "error")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/voice")
def play_project_voice(project_id):
    project = get_project(project_id)
    if not project or not project.get("voice_path"):
        return "Voice narration not found", 404
    audio_path = Path(app.root_path) / project["voice_path"]
    if not audio_path.is_file():
        return "Audio file not found", 404
    return send_file(audio_path, mimetype="audio/wav")


@app.route("/projects/<int:project_id>/generate-subtitles", methods=["POST"])
def generate_project_subtitles(project_id):
    project = get_project(project_id)
    if not project:
        return "Project not found", 404
    try:
        voice_path = project.get("voice_path", "")
        if not voice_path:
            flash("Generate voice narration before creating subtitles.", "error")
            return redirect(url_for("project_detail", project_id=project_id))
        audio_path = Path(app.root_path) / voice_path
        if not audio_path.is_file():
            flash("Narration audio file not found. Generate voice again.", "error")
            return redirect(url_for("project_detail", project_id=project_id))
        output_path = Path(app.root_path) / "projects" / str(project_id) / "subtitles.srt"
        generate_subtitles(str(audio_path), str(output_path))
        update_project(project_id, subtitle_path=f"projects/{project_id}/subtitles.srt", error="")
        flash("Synchronized subtitles generated successfully!", "success")
    except Exception as exc:
        app.logger.exception("Subtitle generation failed")
        flash(f"Subtitle generation failed: {exc}", "error")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/subtitles")
def download_project_subtitles(project_id):
    project = get_project(project_id)
    if not project or not project.get("subtitle_path"):
        return "Subtitle file not found", 404
    subtitle_path = Path(app.root_path) / project["subtitle_path"]
    if not subtitle_path.is_file():
        return "Subtitle file not found", 404
    return send_file(subtitle_path, mimetype="text/plain", as_attachment=True, download_name="subtitles.srt")


def _audio_duration_seconds(audio_path):
    with wave.open(str(audio_path), "rb") as audio:
        frames = audio.getnframes()
        rate = audio.getframerate()
        if rate <= 0:
            raise ValueError("Narration WAV has an invalid sample rate.")
        return frames / float(rate)


def _scene_durations(scenes, total_seconds):
    raw = []
    for scene in scenes:
        try:
            value = float(scene.get("duration_seconds", 0))
        except (TypeError, ValueError):
            value = 0
        raw.append(max(1.0, value))
    total = sum(raw)
    if total <= 0:
        raw = [1.0] * len(scenes)
        total = float(len(scenes))
    durations = [max(1, round(total_seconds * value / total)) for value in raw]
    # Correct rounding drift so the visuals end with the narration.
    durations[-1] = max(1, durations[-1] + round(total_seconds) - sum(durations))
    return durations


@app.route("/projects/<int:project_id>/generate-final-video", methods=["POST"])
def generate_project_final_video(project_id):
    project = get_project(project_id)
    if not project:
        return "Project not found", 404

    try:
        if not project.get("script"):
            raise ValueError("Generate the story script before rendering the final video.")

        project_dir = Path(app.root_path) / "projects" / str(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)

        # Voice is required. Generate it automatically if it is not ready yet.
        voice_rel = project.get("voice_path") or f"projects/{project_id}/narration.wav"
        voice_file = Path(app.root_path) / voice_rel
        if not voice_file.is_file():
            generate_voice(project["script"], str(voice_file))
            update_project(project_id, voice_path=voice_rel)

        # Whisper subtitles are created automatically when the user has not generated them yet.
        subtitle_rel = project.get("subtitle_path") or f"projects/{project_id}/subtitles.srt"
        subtitle_file = Path(app.root_path) / subtitle_rel
        if not subtitle_file.is_file():
            generate_subtitles(str(voice_file), str(subtitle_file))
            update_project(project_id, subtitle_path=subtitle_rel)

        try:
            scenes = json.loads(project.get("scenes") or "[]")
        except json.JSONDecodeError as exc:
            raise ValueError("Scene breakdown is not valid JSON. Regenerate the story plan.") from exc
        if not isinstance(scenes, list) or not scenes:
            raise ValueError("No scene breakdown found. Generate the story plan again.")

        narration_duration = _audio_duration_seconds(voice_file)
        durations = _scene_durations(scenes, narration_duration)
        visuals_dir = project_dir / "scene_visuals"
        clips_dir = project_dir / "scene_clips"
        visuals_dir.mkdir(exist_ok=True)
        clips_dir.mkdir(exist_ok=True)

        clip_paths = []
        for index, scene in enumerate(scenes):
            image_path = visuals_dir / f"scene_{index + 1:02d}.jpg"
            clip_path = clips_dir / f"scene_{index + 1:02d}.mp4"
            create_storyboard_image(scene, image_path, project["title"], index, len(scenes))
            motion = select_camera_motion(scene, index)
            create_scene_clip(
                str(image_path),
                str(clip_path),
                duration=durations[index],
                motion=motion
                )
            clip_paths.append(str(clip_path))

        visual_rel = f"projects/{project_id}/scene_video.mp4"
        visual_file = Path(app.root_path) / visual_rel
        combine_scene_clips(clip_paths, str(visual_file))

        final_rel = f"projects/{project_id}/final_video.mp4"
        final_file = Path(app.root_path) / final_rel
        try:
            music_file = get_music_for_style(project.get("genre", "ambient"))
        except FileNotFoundError:
            music_file = None

        create_final_video(
            str(visual_file),
            str(voice_file),
            str(subtitle_file),
            str(final_file),
            music_path=music_file
        )

        update_project(
            project_id,
            visual_video_path=visual_rel,
            final_video_path=final_rel,
            status="Video ready",
            error=""
        )
        flash("Final video rendered successfully. Your MP4 is ready!", "success")

    except Exception as exc:
        app.logger.exception("Final video rendering failed")
        update_project(project_id, error=str(exc))
        flash(f"Final video rendering failed: {exc}", "error")

    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/final-video")
def play_project_final_video(project_id):
    project = get_project(project_id)
    if not project or not project.get("final_video_path"):
        return "Final video not found", 404
    video_path = Path(app.root_path) / project["final_video_path"]
    if not video_path.is_file():
        return "Final video file not found", 404
    return send_file(video_path, mimetype="video/mp4")


@app.route("/projects/<int:project_id>/download-final-video")
def download_project_final_video(project_id):
    project = get_project(project_id)
    if not project or not project.get("final_video_path"):
        return "Final video not found", 404
    video_path = Path(app.root_path) / project["final_video_path"]
    if not video_path.is_file():
        return "Final video file not found", 404
    return send_file(video_path, mimetype="video/mp4", as_attachment=True, download_name=f"cineforge_project_{project_id}.mp4")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
