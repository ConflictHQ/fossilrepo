import json

from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def dashboard(request):
    from fossil.models import FossilRepository
    from fossil.reader import FossilReader
    from projects.models import Project

    # Aggregate stats across all projects
    total_projects = Project.objects.count()
    total_checkins = 0
    total_tickets = 0
    total_wiki = 0
    system_activity = []  # weekly commit counts across all repos
    recent_across_all = []

    repos = FossilRepository.objects.filter(deleted_at__isnull=True)
    for repo in repos:
        if not repo.exists_on_disk:
            continue
        try:
            with FossilReader(repo.full_path) as reader:
                meta = reader.get_metadata()
                total_checkins += meta.checkin_count
                total_tickets += meta.ticket_count
                total_wiki += meta.wiki_page_count

                activity = reader.get_commit_activity(weeks=26)
                if not system_activity:
                    system_activity = [c["count"] for c in activity]
                else:
                    for i, c in enumerate(activity):
                        if i < len(system_activity):
                            system_activity[i] += c["count"]

                commits = reader.get_timeline(limit=3, event_type="ci")
                for c in commits:
                    recent_across_all.append({"project": repo.project, "entry": c})
        except Exception:
            continue

    # Sort recent across all by timestamp, take top 10
    recent_across_all.sort(key=lambda x: x["entry"].timestamp, reverse=True)
    recent_across_all = recent_across_all[:10]

    return render(
        request,
        "dashboard.html",
        {
            "total_projects": total_projects,
            "total_checkins": total_checkins,
            "total_tickets": total_tickets,
            "total_wiki": total_wiki,
            "total_repos": repos.count(),
            "system_activity_json": json.dumps(system_activity),
            "recent_across_all": recent_across_all,
        },
    )
