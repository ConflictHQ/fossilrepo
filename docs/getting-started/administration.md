# Administration

## User Management

Navigate to **Admin > Members** in the sidebar.

### Creating Users
1. Click "Create User"
2. Fill in username, email, name, password
3. Optionally assign an org role
4. User is automatically added as an organization member

### Editing Users
Click a username to view their profile, then "Edit" to change:
- Name, email
- Active/inactive status
- Staff status (access to Super Admin)
- Org role assignment

### Changing Passwords
From the user detail page, click "Change Password". Admins can change any user's password. Users can change their own password from their profile page.

### Deactivating Users
Edit the user and uncheck "Active". This prevents login without deleting the account. The user's history and contributions are preserved.

## Roles

Navigate to **Admin > Roles** in the sidebar.

### Predefined Roles

| Role | Access Level |
|------|-------------|
| Admin | Full access to everything |
| Manager | Manage projects, teams, members, pages |
| Developer | Contribute: view projects, create tickets |
| Viewer | Read-only access to all content |

### Custom Roles
Click "Create Role" to define a custom role with a specific permission set. The permission picker groups permissions by app (Organization, Projects, Pages, Fossil).

### Initializing Roles
If no roles exist, click "Initialize Roles" to create the four predefined roles. This runs the `seed_roles` management command.

### How Roles Work
Each role maps to a Django Group with the same permissions. When a user is assigned a role, their previous role group is removed and the new one added. Permissions are synced automatically.

## Teams

Navigate to **Admin > Teams** in the sidebar.

Teams are groups of users that can be assigned to projects with specific access levels.

### Creating Teams
1. Click "New Team"
2. Enter name and description
3. Add members from the user list

### Assigning Teams to Projects
1. Go to the project overview
2. Click the project name > Teams section
3. Click "Add Team"
4. Select team and role (read/write/admin)

## Project Groups

Navigate to **Admin > Groups** in the sidebar.

Groups organize related projects together in the sidebar. For example, "Fossil SCM" group might contain the source code repo, forum repo, and docs repo.

### Creating Groups
1. Click "Create Group"
2. Enter name and description
3. Assign projects to the group via the project edit form

## Organization Settings

Navigate to **Admin > Settings** in the sidebar.

Configure the organization name, website, and description. This appears in the site header and various admin pages.

## Audit Log

Navigate to **Admin > Audit Log** in the sidebar.

Shows all model changes across the application, powered by django-simple-history. Filter by model type to see changes to specific entities.

## Super Admin

Navigate to **Admin > Super Admin** in the sidebar.

This is Django's built-in admin interface. Use it for:
- Direct database access to any model
- Constance runtime settings
- Celery task results and beat schedule
- Advanced permission management
- Data import/export

Most day-to-day operations should be done through the main UI, not Super Admin.

## Feature Flags

Feature flags let you enable or disable optional functionality at runtime without redeploying. All flags default to **off** so you can ship a minimal install and turn things on as you need them.

Navigate to **Super Admin → Constance → Config** (or `/admin/constance/config/`) and look for the **Features** section.

| Flag | What it enables |
|---|---|
| `FEATURE_CHAT` | Per-project chat rooms — real-time messaging backed by HTMX polling |
| `FEATURE_RELEASES` | Releases tab — tag-based releases with downloadable assets and source archives |
| `FEATURE_SYNC` | Sync tab — Git mirror configuration (GitHub/GitLab OAuth, push/pull schedules) |
| `FEATURE_FILES` | Files tab — unversioned file storage attached to a project (like GitHub release assets but not version-controlled) |

Changes take effect immediately — no restart needed.

!!! tip "Recommended starting point"
    Enable `FEATURE_RELEASES`, `FEATURE_SYNC`, and `FEATURE_FILES` once your instance is stable. Leave `FEATURE_CHAT` off until you need it — the model and routes are present but hidden.

## Project Settings

Each project has its own settings tab (visible to project admins):

### Repository Info
- Filename, file size, project code, checkin/ticket/wiki counts

### Remote URL
- Configure upstream Fossil remote for pull/push/sync

### Clone URLs
- HTTP clone URL for users
- SSH clone URL

### Tokens
- Project-scoped API tokens for CI/CD integration

### Branch Protection
- Per-branch rules: restrict push, require CI status checks

### Webhooks
- Outbound webhooks on repository events

## Notification Settings

Users configure their own notification preferences at **/auth/notifications/**:

- **Delivery mode**: Immediate, Daily Digest, Weekly Digest, Off
- **Event types**: Checkins, Tickets, Wiki, Releases, Forum

Admins can view user preferences via Super Admin.
