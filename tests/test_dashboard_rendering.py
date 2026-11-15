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
        self.assertIn("Попытка 1 · ОШИБКА", data["architecture"])
        self.assertIn("Попытка 2 · УСПЕШНО", data["architecture"])
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

        for filename in ("build-all.yml",):
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
            self.assertIn("Снимки кеша", data["architecture"])
            self.assertNotIn("Ожидает", data["architecture"])
            self.assertEqual(data["timeline"].count('class="bar '), len(jobs))


    def execute_view(self, code):
        script = (ROOT / "index.html").read_text().split("<script>", 1)[1].split("</script>", 1)[0]
        script = script[:script.index('  $("refresh").addEventListener')]
        setup = """
          const elements={};
          document.getElementById=id => elements[id] ||= {innerHTML:"",textContent:"",hidden:false,
            querySelectorAll:()=>[],setAttribute:()=>{},classList:{add:()=>{},remove:()=>{}}};
        """
        result = subprocess.run(["node", "-e", "global.document={getElementById:()=>({})};" + script + setup + code + "})();"],
                                capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_month_view_filters_and_untrusted_titles_are_escaped(self):
        data = self.execute_view("""
          const run={id:42,run_number:42,status:"completed",conclusion:"failure",run_attempt:1,
            created_at:"2026-11-15T10:00:00Z",first_started_at:"2026-11-15T10:00:00Z",
            completed_at:"2026-11-15T11:00:00Z",duration_ms:3600000,runner_ms:7200000};
          model={repository:"fixture/argon",builds:[{kind:"test",run,pr_number:7,jobs:[],artifacts:[]}],
            selectedRunId:42,days:[{date:"2026-11-15",builds:1,test:1,release:0,legacy:0,failed:1}],
            releases:[],open_prs:[{number:7,title:'<img src=x onerror=alert(1)>',html_url:'javascript:alert(1)',test_runs:[]}],
            unreleased:{pr_count:0},window:{start:"2026-10-17",end:"2026-11-15"}};
          renderModel();
          const all=filteredBuilds().length;filters.kind="release";const release=filteredBuilds().length;
          filters.kind="test";const test=filteredBuilds().length;
          process.stdout.write(JSON.stringify({all,release,test,history:elements.history.innerHTML,
            prs:elements['open-prs'].innerHTML,calendar:elements['month-grid'].innerHTML,
            link:link('https://github.com/fixture/argon','Открыть')}));
        """)
        self.assertEqual((data["all"],data["release"],data["test"]),(1,0,1))
        self.assertIn("Тестовая",data["history"])
        self.assertIn("ОШИБКА",data["history"])
        self.assertIn("&lt;img",data["prs"])
        self.assertNotIn('href="javascript:',data["prs"])
        self.assertNotIn("<img",data["prs"])
        self.assertIn('data-day="2026-11-15"',data["calendar"])
        self.assertIn('href="https://github.com',data["link"])

    def test_active_duration_advances_after_last_snapshot_and_empty_month_is_valid(self):
        data = self.execute_view("""
          Date.now=()=>Date.parse("2026-11-15T12:00:00Z");
          const duration=durationMs({status:"in_progress",first_started_at:"2026-11-15T10:00:00Z",
            updated_at:"2026-11-15T10:10:00Z",duration_ms:600000});
          model={repository:"fixture/argon",builds:[],days:[],releases:[],open_prs:[],unreleased:{}};
          renderModel();
          process.stdout.write(JSON.stringify({duration,empty:elements.history.innerHTML,hidden:elements['detail-panel'].hidden}));
        """)
        self.assertEqual(data["duration"],7200000)
        self.assertIn("пока нет",data["empty"])
        self.assertTrue(data["hidden"])
