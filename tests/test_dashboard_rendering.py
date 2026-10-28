"""Execute the dashboard's actual JS against retry and legacy data."""
import json
import re
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js required for dashboard rendering")
class DashboardRenderingTests(unittest.TestCase):
    def test_retry_history_totals_and_disjoint_timeline_bars(self):
        script = (ROOT / "index.html").read_text().split("<script>", 1)[1].split("</script>", 1)[0]
        # Replace only bootstrap side effects, leaving all rendering code intact.
        start = script.index('  $("refresh").addEventListener')
        script = script[:start] + '''
          const elements={};
          document.getElementById=id => elements[id] ||= {};
          const first={id:1,run_attempt:1,status:"completed",conclusion:"failure",
            started_at:"2026-10-05T01:00:00Z",completed_at:"2026-10-05T07:00:00Z",
            steps:[{name:"Warm local compiler cache (checkpoint 1/20)",status:"completed",conclusion:"success"}]};
          const second={...first,id:2,run_attempt:2,conclusion:"success",
            started_at:"2026-10-05T10:00:00Z",completed_at:"2026-10-05T13:00:00Z"};
          const job={...second,name:"Build and verify arm64 / build",duration_ms:9*3600000,
            started_at:first.started_at,attempts:[first,second]};
          const run={status:"completed",first_started_at:first.started_at,
            run_started_at:second.started_at,completed_at:second.completed_at,duration_ms:12*3600000};
          renderTimeline(run,[job]);
          process.stdout.write(JSON.stringify({runDuration:durationMs(run),jobDuration:durationMs(job),
            legacyDuration:durationMs(second),warm:checkpointStats(job),
            architecture:renderArchitecture("arm64-v8a",job),timeline:elements.timeline.innerHTML}));
        })();
        '''
        result = subprocess.run(["node", "-e", "global.document={getElementById:()=>({})};\n" + script],
                                capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        self.assertEqual(data["runDuration"], 12 * 3600_000)
        self.assertEqual(data["jobDuration"], 9 * 3600_000)
        self.assertEqual(data["legacyDuration"], 3 * 3600_000)
        self.assertEqual(data["warm"]["success"], 2)
        self.assertIn("Attempt 1 · FAILED", data["architecture"])
        self.assertIn("Attempt 2 · SUCCESS", data["architecture"])
        self.assertEqual(data["timeline"].count('class="bar '), 2)
        self.assertIn('left:75.00%', data["timeline"])

    def test_current_workflow_names_and_continuous_steps_render_successfully(self):
        workflows = ROOT / ".github/workflows"

        def job_name(filename, job):
            return re.search(r"(?m)^  " + job + r":\n    name: (.+)$",
                             (workflows / filename).read_text())[1]

        arch_workflow = (workflows / "build-unified-arch.yml").read_text()
        blocks = re.split(r"(?m)^      - name: ", arch_workflow)[1:]

        def step_name(step_id):
            return next(block.splitlines()[0] for block in blocks
                        if f"\n        id: {step_id}\n" in block)

        def step(name, start, end):
            return {"name": name, "status": "completed", "conclusion": "success",
                    "started_at": f"2026-10-28T00:{start}:00Z",
                    "completed_at": f"2026-10-28T00:{end}:00Z"}

        for filename in ("build-all.yml", "build-all-no-publish.yml"):
            jobs = []
            for index, arch in enumerate(("arm64", "arm")):
                name = job_name(filename, "build").replace("${{ matrix.arch }}", arch)
                name += " / " + job_name("build-unified-arch.yml", "build").replace("${{ inputs.arch }}", arch)
                checkpoints = [block.splitlines()[0] for block in blocks
                               if block.startswith("Сохранить прогресс за ")]
                steps = [step(step_name("cache"), "00", "01"),
                         step(step_name("start"), "01", "01")]
                steps += [step(name, "01", "02") for name in checkpoints]
                steps += [step(step_name("build"), "03", "03")]
                jobs.append({"id": index + 1, "name": name, "steps": steps,
                             "status": "completed", "conclusion": "success",
                             "started_at": "2026-10-28T00:00:00Z", "completed_at": "2026-10-28T00:04:00Z"})
            prepared_name = re.search(r"(?m)^    name: (.+)$",
                                     (workflows / "build-chromium-image.yml").read_text())[1]
            jobs.append({**jobs[0], "id": 3, "name": job_name(filename, "image") + " / " + prepared_name})
            if filename == "build-all.yml":
                publish_name = job_name(filename, "publish")
                jobs.append({**jobs[0], "id": 4, "name": publish_name + " / " + publish_name})
            script = (ROOT / "index.html").read_text().split("<script>", 1)[1].split("</script>", 1)[0]
            script = script[:script.index('  $("refresh").addEventListener')]
            script += "const jobs=" + json.dumps(jobs) + ";" + """
              const elements={};
              document.getElementById=id => elements[id] ||= {};
              const run={...jobs[0],run_number:42};
              renderMetrics(run,jobs);renderPipeline(run,jobs);renderArchitectures(jobs);renderTimeline(run,jobs);
              const parts=findJobs(jobs);
              process.stdout.write(JSON.stringify({parts:Object.fromEntries(Object.entries(parts).map(([k,v])=>[k,v?.id??null])),
                apk:apkDuration(parts.arm64),restore:stepDuration(parts.arm64,"Restore compiler cache","Восстановить кеш компиляции"),
                checkpoints:checkpointStats(parts.arm64),architecture:elements.architectures.innerHTML,
                pipeline:elements.pipeline.innerHTML,timeline:elements.timeline.innerHTML}));
            })();
            """
            result = subprocess.run(["node", "-e", "global.document={getElementById:()=>({})};\n" + script],
                                    capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            self.assertEqual(data["parts"], {"arm64": 1, "arm": 2, "gate": 3,
                                           "publish": 4 if filename == "build-all.yml" else None})
            self.assertEqual(data["apk"], 120000)
            self.assertEqual(data["restore"], 60000)
            self.assertEqual(data["checkpoints"]["success"], 5)
            self.assertEqual(data["checkpoints"]["total"], 5)
            self.assertIn("Checkpoints", data["architecture"])
            self.assertNotIn("Waiting", data["architecture"])
            self.assertEqual(data["timeline"].count('class="bar '), len(jobs))
