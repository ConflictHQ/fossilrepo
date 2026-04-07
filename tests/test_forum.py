import pytest
from django.contrib.auth.models import User
from django.test import Client

from fossil.forum import ForumPost
from fossil.models import FossilRepository
from organization.models import Team
from projects.models import ProjectTeam


@pytest.fixture
def fossil_repo_obj(sample_project):
    """Return the auto-created FossilRepository for sample_project."""
    return FossilRepository.objects.get(project=sample_project, deleted_at__isnull=True)


@pytest.fixture
def forum_thread(fossil_repo_obj, admin_user):
    """Create a root forum post (thread starter)."""
    post = ForumPost.objects.create(
        repository=fossil_repo_obj,
        title="Test Thread",
        body="This is a test thread body.",
        created_by=admin_user,
    )
    post.thread_root = post
    post.save(update_fields=["thread_root", "updated_at", "version"])
    return post


@pytest.fixture
def forum_reply_post(forum_thread, admin_user):
    """Create a reply to the thread."""
    return ForumPost.objects.create(
        repository=forum_thread.repository,
        title="",
        body="This is a reply.",
        parent=forum_thread,
        thread_root=forum_thread,
        created_by=admin_user,
    )


@pytest.fixture
def writer_user(db, admin_user, sample_project):
    """User with write access but not admin."""
    writer = User.objects.create_user(username="writer", password="testpass123")
    team = Team.objects.create(name="Writers", organization=sample_project.organization, created_by=admin_user)
    team.members.add(writer)
    ProjectTeam.objects.create(project=sample_project, team=team, role="write", created_by=admin_user)
    return writer


@pytest.fixture
def writer_client(writer_user):
    client = Client()
    client.login(username="writer", password="testpass123")
    return client


# --- ForumPost Model Tests ---


@pytest.mark.django_db
class TestForumPostModel:
    def test_create_thread(self, forum_thread):
        assert forum_thread.pk is not None
        assert str(forum_thread) == "Test Thread"
        assert forum_thread.is_reply is False
        assert forum_thread.thread_root == forum_thread

    def test_create_reply(self, forum_reply_post, forum_thread):
        assert forum_reply_post.pk is not None
        assert forum_reply_post.is_reply is True
        assert forum_reply_post.parent == forum_thread
        assert forum_reply_post.thread_root == forum_thread

    def test_soft_delete(self, forum_thread, admin_user):
        forum_thread.soft_delete(user=admin_user)
        assert forum_thread.is_deleted
        assert ForumPost.objects.filter(pk=forum_thread.pk).count() == 0
        assert ForumPost.all_objects.filter(pk=forum_thread.pk).count() == 1

    def test_ordering(self, fossil_repo_obj, admin_user):
        """Posts are ordered by created_at ascending."""
        p1 = ForumPost.objects.create(repository=fossil_repo_obj, title="First", body="body", created_by=admin_user)
        p2 = ForumPost.objects.create(repository=fossil_repo_obj, title="Second", body="body", created_by=admin_user)
        posts = list(ForumPost.objects.filter(repository=fossil_repo_obj))
        assert posts[0] == p1
        assert posts[1] == p2

    def test_reply_str_fallback(self, forum_reply_post):
        """Replies with no title use created_by in __str__."""
        result = str(forum_reply_post)
        assert "admin" in result


# --- Forum List View Tests ---


@pytest.mark.django_db
class TestForumListView:
    def test_list_empty(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/forum/")
        assert response.status_code == 200
        assert "No forum posts" in response.content.decode()

    def test_list_with_django_thread(self, admin_client, sample_project, forum_thread):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/forum/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Test Thread" in content
        assert "local" in content  # Django posts show "local" badge

    def test_new_thread_button_visible_for_writers(self, writer_client, sample_project, fossil_repo_obj):
        response = writer_client.get(f"/projects/{sample_project.slug}/fossil/forum/")
        assert response.status_code == 200
        assert "New Thread" in response.content.decode()

    def test_new_thread_button_hidden_for_no_perm(self, no_perm_client, sample_project, fossil_repo_obj):
        # Make project public so no_perm can read it
        sample_project.visibility = "public"
        sample_project.save()
        response = no_perm_client.get(f"/projects/{sample_project.slug}/fossil/forum/")
        assert response.status_code == 200
        assert "New Thread" not in response.content.decode()

    def test_list_denied_for_private_project_no_perm(self, no_perm_client, sample_project):
        response = no_perm_client.get(f"/projects/{sample_project.slug}/fossil/forum/")
        assert response.status_code == 403


# --- Forum Create View Tests ---


@pytest.mark.django_db
class TestForumCreateView:
    def test_get_form(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/forum/create/")
        assert response.status_code == 200
        assert "New Thread" in response.content.decode()

    def test_create_thread(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/forum/create/",
            {"title": "My New Thread", "body": "Thread body content"},
        )
        assert response.status_code == 302
        post = ForumPost.objects.get(title="My New Thread")
        assert post.body == "Thread body content"
        assert post.thread_root == post
        assert post.parent is None
        assert post.created_by.username == "admin"

    def test_create_denied_for_no_perm(self, no_perm_client, sample_project):
        response = no_perm_client.post(
            f"/projects/{sample_project.slug}/fossil/forum/create/",
            {"title": "Nope", "body": "Should fail"},
        )
        assert response.status_code == 403

    def test_create_denied_for_anon(self, client, sample_project):
        response = client.get(f"/projects/{sample_project.slug}/fossil/forum/create/")
        assert response.status_code == 302  # redirect to login

    def test_create_empty_fields_stays_on_form(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/forum/create/",
            {"title": "", "body": ""},
        )
        assert response.status_code == 200  # stays on form, no redirect


# --- Forum Reply View Tests ---


@pytest.mark.django_db
class TestForumReplyView:
    def test_reply_form(self, admin_client, sample_project, forum_thread):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/forum/{forum_thread.pk}/reply/")
        assert response.status_code == 200
        assert "Reply to:" in response.content.decode()

    def test_post_reply(self, admin_client, sample_project, forum_thread):
        response = admin_client.post(
            f"/projects/{sample_project.slug}/fossil/forum/{forum_thread.pk}/reply/",
            {"body": "This is my reply"},
        )
        assert response.status_code == 302
        reply = ForumPost.objects.filter(parent=forum_thread).first()
        assert reply is not None
        assert reply.body == "This is my reply"
        assert reply.thread_root == forum_thread
        assert reply.is_reply is True

    def test_reply_denied_for_no_perm(self, no_perm_client, sample_project, forum_thread):
        response = no_perm_client.post(
            f"/projects/{sample_project.slug}/fossil/forum/{forum_thread.pk}/reply/",
            {"body": "Should fail"},
        )
        assert response.status_code == 403

    def test_reply_denied_for_anon(self, client, sample_project, forum_thread):
        response = client.post(
            f"/projects/{sample_project.slug}/fossil/forum/{forum_thread.pk}/reply/",
            {"body": "Should redirect"},
        )
        assert response.status_code == 302  # redirect to login

    def test_reply_to_nonexistent_post(self, admin_client, sample_project, fossil_repo_obj):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/forum/99999/reply/")
        assert response.status_code == 404


# --- Forum Thread View Tests ---


@pytest.mark.django_db
class TestForumThreadView:
    def test_django_thread_detail(self, admin_client, sample_project, forum_thread):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/forum/{forum_thread.pk}/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Test Thread" in content
        assert "test thread body" in content.lower()

    def test_django_thread_with_replies(self, admin_client, sample_project, forum_thread, forum_reply_post):
        response = admin_client.get(f"/projects/{sample_project.slug}/fossil/forum/{forum_thread.pk}/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "This is a reply" in content

    def test_thread_shows_reply_form_for_writers(self, writer_client, sample_project, forum_thread):
        response = writer_client.get(f"/projects/{sample_project.slug}/fossil/forum/{forum_thread.pk}/")
        assert response.status_code == 200
        assert "Post Reply" in response.content.decode()

    def test_thread_hides_reply_form_for_no_perm(self, no_perm_client, sample_project, forum_thread):
        sample_project.visibility = "public"
        sample_project.save()
        response = no_perm_client.get(f"/projects/{sample_project.slug}/fossil/forum/{forum_thread.pk}/")
        assert response.status_code == 200
        assert "Post Reply" not in response.content.decode()

    def test_thread_denied_for_private_no_perm(self, no_perm_client, sample_project, forum_thread):
        response = no_perm_client.get(f"/projects/{sample_project.slug}/fossil/forum/{forum_thread.pk}/")
        assert response.status_code == 403
