"""The frontend bundle that deploys is the one the E2E tests ran against (#147).

String checks on the workflow and Playwright config: they catch the steps being
dropped or the deploy going back to its own build. The steps themselves run on
every CI run and deploy.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEPLOY_YML = (REPO / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
PLAYWRIGHT_CONFIG = (REPO / "ui" / "playwright.config.js").read_text(encoding="utf-8")


def _job(name: str) -> str:
    """Text of one top-level job in deploy.yml."""
    start = DEPLOY_YML.index(f"\n  {name}:\n")
    rest = DEPLOY_YML[start + 1:]
    following = [rest.index(f"\n  {other}:\n") for other in ("test", "e2e", "deploy")
                 if other != name and f"\n  {other}:\n" in rest]
    return rest[:min(following)] if following else rest


class TestE2eRunsTheProductionBuild:
    def test_playwright_serves_a_fresh_production_build(self):
        assert "npm run build && npm run preview -- --port ${PORT} --strictPort" in PLAYWRIGHT_CONFIG
        assert "npm run dev" not in PLAYWRIGHT_CONFIG
        assert "reuseExistingServer: false" in PLAYWRIGHT_CONFIG

    def test_failed_tests_keep_trace_and_screenshot(self):
        assert 'trace: "retain-on-failure"' in PLAYWRIGHT_CONFIG
        assert 'screenshot: "only-on-failure"' in PLAYWRIGHT_CONFIG


class TestDeployShipsTheTestedBundle:
    def test_e2e_job_uploads_the_bundle_it_tested(self):
        e2e = _job("e2e")
        run = e2e.index("npx playwright test")
        upload = e2e.index("name: ui-dist")
        assert run < upload
        assert "path: ui/dist/" in e2e[upload:]
        assert "if-no-files-found: error" in e2e[upload:]

    def test_e2e_job_uploads_results_on_failure(self):
        e2e = _job("e2e")
        step = e2e.index("name: Upload Playwright results")
        assert "if: failure()" in e2e[step:step + 200]
        assert "path: ui/test-results/" in e2e[step:]

    def test_deploy_job_downloads_instead_of_building(self):
        deploy = _job("deploy")
        assert "needs: [test, e2e]" in deploy
        download = deploy.index("actions/download-artifact@")
        assert "name: ui-dist" in deploy[download:download + 200]
        assert "path: ui/dist" in deploy[download:download + 200]
        assert deploy.index("tar czf /tmp/dist.tar.gz -C ui dist") > download
        assert "npm run build" not in deploy
        assert "npm ci" not in deploy
