import pytest

from ncprd_pool.discover import DiscoveryError, choose_schedule_link
from conftest import FALL_URL, LANDING_URL

PAGE = "<html><body><h1>North Clackamas Aquatic Park</h1>{links}" + "<p>filler</p>" * 300 + "</body></html>"


def test_finds_current_schedule_on_real_landing_page(landing_html, cfg):
    c = choose_schedule_link(landing_html, LANDING_URL, cfg.discovery)
    assert c.url == FALL_URL
    assert "Drop-in Schedule" in c.text


def test_follows_renamed_link(landing_html, cfg):
    new = "https://cms-assets.ncprd.org/Winter_2027_NCAP_Pool_Times_ffff0000.pdf"
    c = choose_schedule_link(landing_html.replace(FALL_URL, new), LANDING_URL, cfg.discovery)
    assert c.url == new


def test_resolves_relative_links_and_strips_fragment(cfg):
    html = PAGE.format(links='<a href="/files/pool.pdf#page=2">Aquatic Park Drop-in Schedule</a>')
    assert choose_schedule_link(html, LANDING_URL, cfg.discovery).url == "https://ncprd.org/files/pool.pdf"


def test_missing_schedule_link_fails(cfg):
    html = PAGE.format(links='<a href="/programs/aquatics/drop-in-aquatic-fitness">Drop-in Aquatic Fitness</a>'
                             '<a href="/lessons.pdf">Swim Lessons Schedule</a>')
    with pytest.raises(DiscoveryError, match="no drop-in schedule link"):
        choose_schedule_link(html, LANDING_URL, cfg.discovery)


def test_two_equally_good_links_fail(cfg):
    html = PAGE.format(links='<a href="/a.pdf">Drop-in Schedule</a><a href="/b.pdf">Drop-in Schedule</a>')
    with pytest.raises(DiscoveryError, match="ambiguous"):
        choose_schedule_link(html, LANDING_URL, cfg.discovery)


def test_error_page_is_rejected(cfg):
    with pytest.raises(DiscoveryError):
        choose_schedule_link("<html><body>502 Bad Gateway</body></html>", LANDING_URL, cfg.discovery)
