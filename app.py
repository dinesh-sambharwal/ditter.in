from flask import Flask, render_template, redirect, url_for, flash, request
from flask_login import LoginManager, current_user, login_user, logout_user, login_required
import re
import uuid
import sys
from markupsafe import escape, Markup
from models import db, User, Post, Comment, Notification, Poll, PollOption, PollVote, Bookmark, Message, PostImage, MessageImage, Report, ContactMessage
from forms import RegistrationForm, LoginForm, PostForm, EditProfileForm, ResetPasswordRequestForm, ResetPasswordForm, CommentForm, MessageForm
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.jinja_env.add_extension('jinja2.ext.do')
# Secret key for sessions
app.config['SECRET_KEY'] = 'dev-secret-key-12345'
# Database config
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'instance', 'ditter.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

UPLOAD_FOLDER = os.path.join(basedir, 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@app.context_processor
def inject_models():
    return dict(db=db, Message=Message, Bookmark=Bookmark, Post=Post)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# --- Mention helpers ---

MENTION_RE = re.compile(r'@(\w+)')

def parse_mentions(text, sender, post_id=None, comment_id=None):
    """Fire mention notifications for every valid @username found in text."""
    if not text:
        return
    seen = set()
    for match in MENTION_RE.finditer(text):
        username = match.group(1)
        if username in seen:
            continue
        seen.add(username)
        mentioned = User.query.filter_by(username=username).first()
        if mentioned and mentioned.id != sender.id:
            notif = Notification(
                user_id=mentioned.id,
                sender_id=sender.id,
                notification_type='mention',
                post_id=post_id,
                comment_id=comment_id
            )
            db.session.add(notif)

@app.template_filter('linkify_mentions')
def linkify_mentions(text):
    """Replace every @username with a clickable profile link (HTML-safe)."""
    if not text:
        return ''
    safe_text = str(escape(text))
    def replace(m):
        username = m.group(1)
        user = User.query.filter_by(username=username).first()
        if user:
            return (
                f'<a href="/user/{username}" '
                f'class="mention-link" onclick="event.stopPropagation()">@{username}</a>'
            )
        return m.group(0)
    return Markup(MENTION_RE.sub(replace, safe_text))

# --- Character helpers ---

DEFAULT_CHARACTERS = ['astronaut', 'ninja', 'wizard', 'robot', 'pirate', 'cyborg']
DEFAULT_CHARACTERS_SET = set(DEFAULT_CHARACTERS)

@app.template_filter('character_url')
def character_url_filter(user):
    """Return the URL for a user's profile character image, or None if they have no character."""
    if not user or not user.character_filename:
        return None
    name = user.character_filename
    # Strip known extensions for comparison
    stem = name.rsplit('.', 1)[0] if '.' in name else name
    if stem in DEFAULT_CHARACTERS_SET:
        return url_for('static', filename=f'characters/{name}')
    return url_for('static', filename=f'uploads/{name}')

app.jinja_env.globals['DEFAULT_CHARACTERS'] = DEFAULT_CHARACTERS

# --- Routes ---

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    form = PostForm()
    if form.validate_on_submit():
        post = Post(content=form.content.data, author=current_user)
        db.session.add(post)
        db.session.flush()

        # Handle Multiple Images
        if form.images.data:
            for file in form.images.data:
                if hasattr(file, 'filename') and file.filename:
                    filename = secure_filename(file.filename)
                    unique_filename = f"{uuid.uuid4()}_{filename}"
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
                    img = PostImage(post_id=post.id, filename=unique_filename)
                    db.session.add(img)
                    if not post.image_filename:
                        post.image_filename = unique_filename

        # Handle Poll creation
        if form.poll_option1.data and form.poll_option2.data:
            poll = Poll(post_id=post.id, question=form.poll_question.data or form.content.data)
            db.session.add(poll)
            db.session.flush()
            for i, opt_text in enumerate([form.poll_option1.data, form.poll_option2.data, form.poll_option3.data, form.poll_option4.data, form.poll_option5.data, form.poll_option6.data], 1):
                if opt_text:
                    is_correct = (form.poll_correct_option.data == i)
                    option = PollOption(poll_id=poll.id, text=opt_text, is_correct=is_correct)
                    db.session.add(option)
            
        if current_user.is_currently_banned:
            if current_user.is_permanently_banned:
                flash('Your account is permanently banned.', 'danger')
            else:
                flash(f"Your account is restricted until {current_user.banned_until.strftime('%Y-%m-%d %H:%M')}.", 'warning')
            return redirect(url_for('index'))
            
        parse_mentions(form.content.data, current_user, post_id=post.id)
        db.session.commit()
        flash('Your Dit is now live!', 'success')
        return redirect(url_for('index'))

    # Fetch posts, excluding private accounts that the current_user doesn't follow
    page = request.args.get('page', 1, type=int)
    # Get all users the current user follows
    followed_ids = [u.id for u in current_user.followed]
    followed_ids.append(current_user.id) # Include own posts
    
    # Base query: posts from followed users OR public users
    posts_query = Post.query.join(User).filter(
        db.or_(
            User.id.in_(followed_ids),
            User.is_private == False
        )
    ).order_by(Post.timestamp.desc())
    
    posts = posts_query.paginate(page=page, per_page=20)
    return render_template('index.html', title='Home', form=form, posts=posts.items, pagination=posts)

@app.route('/vote/<int:option_id>', methods=['POST'])
@login_required
def vote(option_id):
    option = db.session.get(PollOption, option_id)
    if not option:
        return redirect(url_for('index'))
    
    poll = option.poll
    # Check if user already voted in this poll
    existing_vote = PollVote.query.filter_by(user_id=current_user.id, poll_id=poll.id).first()
    if existing_vote:
        flash('You have already voted in this poll.', 'info')
        return redirect(request.referrer or url_for('index'))
        
    vote_obj = PollVote(user_id=current_user.id, poll_id=poll.id, option_id=option.id)
    db.session.add(vote_obj)
    db.session.commit()
    
    next_page = request.form.get('next')
    return redirect(next_page or request.referrer or url_for('index'))
    return render_template('index.html', title='Home', form=form, posts=posts.items, pagination=posts)

@app.route('/user/<username>')
@login_required
def user(username):
    user = User.query.filter_by(username=username).first_or_404()
    tab = request.args.get('tab', 'dits')
    
    if tab == 'media':
        # Posts that have images (either multiple or single legacy)
        posts = user.posts.outerjoin(PostImage).filter(
            db.or_(Post.image_filename != None, PostImage.id != None)
        ).order_by(Post.is_pinned.desc(), Post.timestamp.desc()).all()
    elif tab == 'likes':
        # Posts the user has liked
        posts = user.liked_posts.order_by(Post.is_pinned.desc(), Post.timestamp.desc()).all()
    else:
        # Default: user's own posts
        posts = user.posts.order_by(Post.is_pinned.desc(), Post.timestamp.desc()).all()
        
    return render_template('profile.html', user=user, posts=posts, tab=tab)

@app.route('/follow/<username>')
@login_required
def follow(username):
    user = User.query.filter_by(username=username).first()
    if user is None:
        flash(f'User {username} not found.', 'danger')
        return redirect(url_for('index'))
    if user == current_user:
        flash('You cannot follow yourself!', 'danger')
        return redirect(url_for('user', username=username))
        
    if user.is_private:
        current_user.request_follow(user)
        notif = Notification(user_id=user.id, sender_id=current_user.id, notification_type='request')
        db.session.add(notif)
        db.session.commit()
        flash(f'Follow request sent to {username}.', 'info')
    else:
        current_user.follow(user)
        notif = Notification(user_id=user.id, sender_id=current_user.id, notification_type='follow')
        db.session.add(notif)
        db.session.commit()
        flash(f'You are following {username}!', 'success')
    return redirect(url_for('user', username=username))

@app.route('/user/<username>/followers')
@login_required
def followers(username):
    user = User.query.filter_by(username=username).first_or_404()
    if user.is_private and user != current_user and not current_user.is_following(user):
        flash('This account is private.', 'danger')
        return redirect(url_for('user', username=username))
    
    users = user.followers.all()
    return render_template('users_list.html', user=user, title="Followers", display_users=users)

@app.route('/user/<username>/following')
@login_required
def following(username):
    user = User.query.filter_by(username=username).first_or_404()
    if user.is_private and user != current_user and not current_user.is_following(user):
        flash('This account is private.', 'danger')
        return redirect(url_for('user', username=username))
    
    users = user.followed.all()
    return render_template('users_list.html', user=user, title="Following", display_users=users)

@app.route('/unfollow/<username>')
@login_required
def unfollow(username):
    user = User.query.filter_by(username=username).first()
    if user is None:
        flash(f'User {username} not found.', 'danger')
        return redirect(url_for('index'))
    if user == current_user:
        flash('You cannot unfollow yourself!', 'danger')
        return redirect(url_for('user', username=username))
        
    if current_user.has_requested_follow(user):
        current_user.cancel_request(user)
        db.session.commit()
        flash(f'Follow request to {username} cancelled.', 'info')
    else:
        current_user.unfollow(user)
        db.session.commit()
        flash(f'You are not following {username}.', 'info')
    return redirect(url_for('user', username=username))

@app.route('/approve/<username>')
@login_required
def approve(username):
    user = User.query.filter_by(username=username).first()
    if user and user.has_requested_follow(current_user):
        user.cancel_request(current_user)
        user.follow(current_user)
        notif = Notification(user_id=user.id, sender_id=current_user.id, notification_type='approve')
        db.session.add(notif)
        db.session.commit()
        flash(f'Approved {username}\'s follow request.', 'success')
    return redirect(url_for('user', username=current_user.username))

@app.route('/deny/<username>')
@login_required
def deny(username):
    user = User.query.filter_by(username=username).first()
    if user and user.has_requested_follow(current_user):
        user.cancel_request(current_user)
        db.session.commit()
        flash(f'Denied {username}\'s follow request.', 'info')
    return redirect(url_for('user', username=current_user.username))

@app.route('/like/<int:post_id>', methods=['POST'])
@login_required
def like(post_id):
    post = db.session.get(Post, post_id)
    if post is None:
        return redirect(url_for('index'))
    if not current_user.has_liked(post):
        current_user.like(post)
        if post.author != current_user:
            notif = Notification(user_id=post.author.id, sender_id=current_user.id, notification_type='like', post_id=post.id)
            db.session.add(notif)
        db.session.commit()
    return redirect(request.referrer or url_for('index'))

@app.route('/unlike/<int:post_id>', methods=['POST'])
@login_required
def unlike(post_id):
    post = db.session.get(Post, post_id)
    if post is None:
        return redirect(url_for('index'))
    if current_user.has_liked(post):
        current_user.unlike(post)
        db.session.commit()
    return redirect(request.referrer or url_for('index'))

@app.route('/like_comment/<int:comment_id>', methods=['POST'])
@login_required
def like_comment(comment_id):
    comment = db.session.get(Comment, comment_id)
    if comment is None:
        return redirect(url_for('index'))
    if not current_user.has_liked_comment(comment):
        current_user.like_comment(comment)
        if comment.author != current_user:
            notif = Notification(user_id=comment.author.id, sender_id=current_user.id, notification_type='comment_like', comment_id=comment.id, post_id=comment.post_id)
            db.session.add(notif)
        db.session.commit()
    return redirect(request.referrer or url_for('index'))

@app.route('/unlike_comment/<int:comment_id>', methods=['POST'])
@login_required
def unlike_comment(comment_id):
    comment = db.session.get(Comment, comment_id)
    if comment is None:
        return redirect(url_for('index'))
    if current_user.has_liked_comment(comment):
        current_user.unlike_comment(comment)
        db.session.commit()
    return redirect(request.referrer or url_for('index'))

@app.route('/post/<int:post_id>', methods=['GET', 'POST'])
@login_required
def view_post(post_id):
    post = db.session.get(Post, post_id)
    if post is None:
        return redirect(url_for('index'))
    # Privacy check
    if post.author.is_private and post.author != current_user and not current_user.is_following(post.author):
        flash('You do not have permission to view this post.', 'danger')
        return redirect(url_for('index'))
        
    form = CommentForm()
    if form.validate_on_submit():
        if current_user.is_currently_banned:
            flash('You are banned from commenting.', 'danger')
            return redirect(url_for('view_post', post_id=post.id))
            
        comment = Comment(body=form.body.data, post=post, author=current_user)
        db.session.add(comment)
        db.session.flush()  # get comment.id before commit
        if post.author != current_user:
            notif = Notification(user_id=post.author.id, sender_id=current_user.id, notification_type='comment', post_id=post.id, comment_id=comment.id)
            db.session.add(notif)
        parse_mentions(form.body.data, current_user, post_id=post.id, comment_id=comment.id)
        db.session.commit()
        flash('Your comment has been published.', 'success')
        return redirect(url_for('view_post', post_id=post.id))
        
    page = request.args.get('page', 1, type=int)
    comments = post.comments.order_by(Comment.is_pinned.desc(), Comment.timestamp.asc()).paginate(page=page, per_page=10)
    return render_template('post.html', post=post, form=form, comments=comments.items, pagination=comments)

@app.route('/redit/<int:post_id>', methods=['POST'])
@login_required
def redit(post_id):
    original_post = db.session.get(Post, post_id)
    if original_post is None:
        return redirect(url_for('index'))
    # Privacy check (cannot redit private posts easily unless authorized, but typically private posts cannot be reditted)
    if original_post.author.is_private:
        flash('You cannot redit posts from a private account.', 'danger')
        return redirect(request.referrer or url_for('index'))
        
    # Prevent reditting the same post multiple times by same user
    existing_redit = Post.query.filter_by(user_id=current_user.id, original_post_id=original_post.id).first()
    if existing_redit:
        # Instead, unredit it
        db.session.delete(existing_redit)
        db.session.commit()
        return redirect(request.referrer or url_for('index'))
        
    new_post = Post(user_id=current_user.id, original_post_id=original_post.id)
    db.session.add(new_post)
    if original_post.author != current_user:
        notif = Notification(user_id=original_post.author.id, sender_id=current_user.id, notification_type='redit', post_id=original_post.id)
        db.session.add(notif)
    db.session.commit()
    flash('Successfully Re-ditted!', 'success')
    return redirect(request.referrer or url_for('index'))

@app.route('/quote/<int:post_id>', methods=['GET', 'POST'])
@login_required
def quote_dit(post_id):
    original_post = db.session.get(Post, post_id)
    if original_post is None:
        return redirect(url_for('index'))
    # Privacy check
    if original_post.author.is_private and not original_post.author == current_user and not current_user.is_following(original_post.author):
        flash('You cannot quote posts from a private account you are not following.', 'danger')
        return redirect(request.referrer or url_for('index'))
        
    form = PostForm()
    if form.validate_on_submit():
        new_post = Post(user_id=current_user.id, original_post_id=original_post.id, content=form.content.data)
        db.session.add(new_post)
        db.session.flush()

        # Handle Multiple Images
        if form.images.data:
            for file in form.images.data:
                if hasattr(file, 'filename') and file.filename:
                    filename = secure_filename(file.filename)
                    unique_filename = f"{uuid.uuid4()}_{filename}"
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
                    img = PostImage(post_id=new_post.id, filename=unique_filename)
                    db.session.add(img)
                    if not new_post.image_filename:
                        new_post.image_filename = unique_filename

        # Handle Poll creation in Quote Dit
        if form.poll_option1.data and form.poll_option2.data:
            poll = Poll(post_id=new_post.id, question=form.poll_question.data or form.content.data)
            db.session.add(poll)
            db.session.flush()
            for i, opt_text in enumerate([form.poll_option1.data, form.poll_option2.data, form.poll_option3.data, form.poll_option4.data, form.poll_option5.data, form.poll_option6.data], 1):
                if opt_text:
                    is_correct = (form.poll_correct_option.data == i)
                    option = PollOption(poll_id=poll.id, text=opt_text, is_correct=is_correct)
                    db.session.add(option)

        if original_post.author != current_user:
            notif = Notification(user_id=original_post.author.id, sender_id=current_user.id, notification_type='quote', post_id=original_post.id)
            db.session.add(notif)
        parse_mentions(form.content.data, current_user, post_id=new_post.id)
        db.session.commit()
        flash('Successfully Quoted!', 'success')
        return redirect(url_for('index'))
        
    return render_template('quote.html', form=form, post=original_post)

@app.route('/delete_post/<int:post_id>', methods=['POST'])
@login_required
def delete_post(post_id):
    post = db.session.get(Post, post_id)
    if post and post.author == current_user:
        post.liked_by = []
        Notification.query.filter_by(post_id=post.id).delete()
        Comment.query.filter_by(post_id=post.id).delete()
        Post.query.filter_by(original_post_id=post.id).update({'original_post_id': None})
        db.session.delete(post)
        db.session.commit()
        flash('Dit has been deleted.', 'success')
    return redirect(request.referrer or url_for('index'))

@app.route('/delete_comment/<int:comment_id>', methods=['POST'])
@login_required
def delete_comment(comment_id):
    comment = db.session.get(Comment, comment_id)
    if comment and comment.author == current_user:
        comment.liked_by = []
        Notification.query.filter_by(comment_id=comment.id).delete()
        db.session.delete(comment)
        db.session.commit()
        flash('Comment has been deleted.', 'success')
    return redirect(request.referrer or url_for('view_post', post_id=(comment.post_id if comment else 0)))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter((User.email == form.username_or_email.data) | (User.username == form.username_or_email.data)).first()
        if user and user.check_password(form.password.data):
            if user.is_permanently_banned:
                flash('Your account has been permanently banned. Please contact administration.', 'danger')
                return redirect(url_for('login'))
            
            if user.is_temporarily_restricted:
                flash(f"Login successful, but your account is restricted from activity until {user.banned_until.strftime('%Y-%m-%d %H:%M')}.", 'warning')
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('index'))
        else:
            flash('Login Unsuccessful. Please check username/email and password', 'danger')
    user_count = User.query.count()
    return render_template('login.html', form=form, user_count=user_count)

@app.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    form = EditProfileForm(current_user.username, current_user.email)
    if form.validate_on_submit():
        if form.new_password.data:
            if not current_user.check_password(form.current_password.data):
                flash('Incorrect current password.', 'danger')
                return render_template('edit_profile.html', form=form)
            current_user.set_password(form.new_password.data)

        # Handle character: custom upload takes priority, then chosen default
        if form.character.data and hasattr(form.character.data, 'filename') and form.character.data.filename:
            filename = secure_filename(form.character.data.filename)
            unique_filename = f"{uuid.uuid4().hex}_{filename}"
            form.character.data.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
            current_user.character_filename = unique_filename
        elif form.chosen_default.data and form.chosen_default.data in DEFAULT_CHARACTERS_SET:
            current_user.character_filename = form.chosen_default.data + '.png'

        if form.cover.data and hasattr(form.cover.data, 'filename') and form.cover.data.filename:
            filename = secure_filename(form.cover.data.filename)
            unique_filename = f"cover_{uuid.uuid4().hex}_{filename}"
            form.cover.data.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
            current_user.cover_filename = unique_filename
        elif form.chosen_cover.data:
            # Handle preset cover from static/images/gradients/
            # We store it as 'gradients/gradient_X.png' or similar logic
            current_user.cover_filename = form.chosen_cover.data

        current_user.username = form.username.data
        current_user.email = form.email.data
        current_user.bio = form.bio.data
        current_user.is_private = form.is_private.data
        current_user.message_privacy = form.message_privacy.data
        db.session.commit()
        flash('Your changes have been saved.', 'success')
        return redirect(url_for('user', username=current_user.username))
    elif request.method == 'GET':
        form.username.data = current_user.username
        form.email.data = current_user.email
        form.bio.data = current_user.bio
        form.is_private.data = current_user.is_private
        form.message_privacy.data = current_user.message_privacy
    return render_template('edit_profile.html', form=form)

@app.route('/reset_password_request', methods=['GET', 'POST'])
def reset_password_request():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = ResetPasswordRequestForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            # Simulate sending an email by flashing the reset link
            reset_token = str(uuid.uuid4())
            # We will just hack it to pass the user ID temporarily, or better yet, skip actual DB tokens and mock it via URL for simplicity
            flash(f'Simulated Email Sent! Use this mock link: /reset_password/{user.id}', 'info')
        else:
            flash('Email not found if registered.', 'info')
        return redirect(url_for('login'))
    return render_template('reset_password_request.html', form=form)

@app.route('/reset_password/<int:user_id>', methods=['GET', 'POST'])
def reset_password(user_id):
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    user = db.session.get(User, user_id)
    if not user:
        return redirect(url_for('index'))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash('Your password has been reset.', 'success')
        return redirect(url_for('login'))
    return render_template('reset_password.html', form=form)

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(username=form.username.data, email=form.email.data)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash('Your account has been created! You are now able to log in', 'success')
        return redirect(url_for('login'))
    return render_template('register.html', form=form)

@app.route('/search')
def search():
    query = request.args.get('q', '').strip()
    if not query:
        return redirect(request.referrer or url_for('index'))
        
    # Search users similar to query
    users = User.query.filter(User.username.ilike(f'%{query}%')).all()
    # Search posts that contain the query string (e.g. hashtags)
    posts = Post.query.filter(Post.content.ilike(f'%{query}%')).order_by(Post.timestamp.desc()).all()
    
    return render_template('search.html', query=query, users=users, posts=posts)

@app.route('/notifications')
@login_required
def notifications():
    page = request.args.get('page', 1, type=int)
    notifs = current_user.notifications.order_by(Notification.timestamp.desc()).paginate(page=page, per_page=20)
    
    # Mark as read
    for n in notifs.items:
        if not n.is_read:
            n.is_read = True
    db.session.commit()
    
    return render_template('notifications.html', notifications=notifs.items, pagination=notifs)

@app.route('/bookmarks')
@login_required
def bookmarks():
    page = request.args.get('page', 1, type=int)
    bookmarks_list = current_user.bookmarks.order_by(Bookmark.timestamp.desc()).paginate(page=page, per_page=20)
    posts = [b.post for b in bookmarks_list.items]
    return render_template('bookmarks.html', title='Bookmarks', posts=posts, pagination=bookmarks_list)

@app.route('/explore/trending')
def trending_full():
    trending = Post.get_trending_hashtags(50)
    return render_template('trending_full.html', trending=trending, title="Trending")

@app.route('/explore/who-to-follow')
@login_required
def who_to_follow_full():
    suggestions = current_user.get_suggestions(50)
    return render_template('who_to_follow_full.html', suggestions=suggestions, title="Who to follow")

@app.route('/bookmark/<int:post_id>', methods=['POST'])
@login_required
def bookmark(post_id):
    post = db.session.get(Post, post_id)
    if post:
        current_user.bookmark(post)
        db.session.commit()
        flash('Dit bookmarked!', 'success')
    return redirect(request.referrer or url_for('index'))

@app.route('/unbookmark/<int:post_id>', methods=['POST'])
@login_required
def unbookmark(post_id):
    post = db.session.get(Post, post_id)
    if post:
        current_user.unbookmark(post)
        db.session.commit()
        flash('Removed from bookmarks.', 'info')
    return redirect(request.referrer or url_for('index'))

@app.route('/messages')
@login_required
def messages():
    # Show conversations (unique users we've chatted with)
    sent_to = db.session.query(Message.recipient_id).filter(Message.sender_id == current_user.id)
    received_from = db.session.query(Message.sender_id).filter(Message.recipient_id == current_user.id)
    chat_user_ids = set([uid[0] for uid in sent_to.union(received_from).all()])
    
    from datetime import datetime
    conversations = []
    request_conversations = []
    
    for uid in chat_user_ids:
        user_obj = db.session.get(User, uid)
        if user_obj:
            last_msg = Message.query.filter(
                db.or_(
                    db.and_(Message.sender_id == current_user.id, Message.recipient_id == user_obj.id),
                    db.and_(Message.sender_id == user_obj.id, Message.recipient_id == current_user.id)
                )
            ).order_by(Message.timestamp.desc()).first()
            
            # A thread is accepted if there's any message in it that is marked as accepted
            is_accepted = Message.query.filter(
                db.or_(
                    db.and_(Message.sender_id == current_user.id, Message.recipient_id == user_obj.id),
                    db.and_(Message.sender_id == user_obj.id, Message.recipient_id == current_user.id)
                ),
                Message.is_accepted == True
            ).first() is not None
            
            unread_count = Message.query.filter_by(sender_id=user_obj.id, recipient_id=current_user.id, is_read=False).count()
            data = {'user': user_obj, 'last_message': last_msg, 'unread_count': unread_count}
            
            # It's a request only if it's NOT accepted AND the most recent message was received by current_user
            if not is_accepted and last_msg and last_msg.recipient_id == current_user.id:
                request_conversations.append(data)
            else:
                conversations.append(data)
            
    conversations.sort(key=lambda x: x['last_message'].timestamp if x['last_message'] else datetime.min, reverse=True)
    request_conversations.sort(key=lambda x: x['last_message'].timestamp if x['last_message'] else datetime.min, reverse=True)
    
    return render_template('messages.html', title='Messages', 
                           conversations=conversations, 
                           request_conversations=request_conversations)

@app.route('/messages/<username>', methods=['GET', 'POST'])
@login_required
def chat(username):
    recipient = User.query.filter_by(username=username).first_or_404()
    
    # Privacy Check
    if recipient.id != current_user.id:
        if recipient.message_privacy == 'none':
            flash(f'@{recipient.username} has disabled direct messaging.', 'danger')
            return redirect(url_for('messages'))
        elif recipient.message_privacy == 'followed':
            if not recipient.is_following(current_user):
                flash(f'Only people @{recipient.username} follows can message them.', 'danger')
                return redirect(url_for('messages'))

    form = MessageForm()
    if form.validate_on_submit():
        # A message is 'accepted'/Primary if:
        # 1. The recipient follows the sender (trusted)
        # 2. There is already an accepted message in this thread
        is_trusted = recipient.is_following(current_user)
        has_accepted_thread = Message.query.filter(
            db.or_(
                db.and_(Message.sender_id == current_user.id, Message.recipient_id == recipient.id),
                db.and_(Message.sender_id == recipient.id, Message.recipient_id == current_user.id)
            ),
            Message.is_accepted == True
        ).first() is not None
        
        is_accepted = is_trusted or has_accepted_thread
        
        # Check if we have content or images
        has_images = False
        if form.images.data:
            for file in form.images.data:
                if hasattr(file, 'filename') and file.filename:
                    has_images = True
                    break
        
        if not form.body.data and not has_images:
            flash('Message cannot be empty.', 'danger')
            return redirect(url_for('chat', username=username))

        msg = Message(
            sender_id=current_user.id, 
            recipient_id=recipient.id, 
            body=form.body.data or '',
            is_accepted=is_accepted,
            is_request=not is_accepted
        )
        db.session.add(msg)
        db.session.flush()

        # Handle Multiple Images for Message
        if form.images.data:
            for file in form.images.data:
                if hasattr(file, 'filename') and file.filename:
                    filename = secure_filename(file.filename)
                    unique_filename = f"{uuid.uuid4()}_{filename}"
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
                    img = MessageImage(message_id=msg.id, filename=unique_filename)
                    db.session.add(img)

        db.session.commit()
        return redirect(url_for('chat', username=username))
    
    msgs = Message.query.filter(
        db.or_(
            db.and_(Message.sender_id == current_user.id, Message.recipient_id == recipient.id),
            db.and_(Message.sender_id == recipient.id, Message.recipient_id == current_user.id)
        )
    ).order_by(Message.timestamp.asc()).all()
    
    # Mark as read
    for m in msgs:
        if m.recipient_id == current_user.id and not m.is_read:
            m.is_read = True
    db.session.commit()
    
    return render_template('chat.html', recipient=recipient, messages=msgs, form=form)
    
@app.route('/accept_message/<username>', methods=['POST'])
@login_required
def accept_message(username):
    user = User.query.filter_by(username=username).first_or_404()
    msgs = Message.query.filter(
        db.or_(
            db.and_(Message.sender_id == current_user.id, Message.recipient_id == user.id),
            db.and_(Message.sender_id == user.id, Message.recipient_id == current_user.id)
        )
    ).all()
    for m in msgs:
        m.is_accepted = True
        m.is_request = False
    db.session.commit()
    flash(f'Message request from @{user.username} accepted!', 'success')
    return redirect(url_for('chat', username=username))

@app.route('/delete_request/<username>', methods=['POST'])
@login_required
def delete_request(username):
    user = User.query.filter_by(username=username).first_or_404()
    # Delete unaccepted messages
    Message.query.filter(
        db.or_(
            db.and_(Message.sender_id == current_user.id, Message.recipient_id == user.id),
            db.and_(Message.sender_id == user.id, Message.recipient_id == current_user.id)
        ),
        Message.is_accepted == False
    ).delete()
    db.session.commit()
    return redirect(url_for('messages'))

@app.route('/pin_post/<int:post_id>', methods=['POST'])
@login_required
def pin_post(post_id):
    post = db.session.get(Post, post_id)
    if post and post.author == current_user:
        # Usually only one pinned post allowed in a profile, so optionally unpin others
        if not post.is_pinned:
            Post.query.filter_by(author=current_user, is_pinned=True).update({'is_pinned': False})
        post.is_pinned = not post.is_pinned
        db.session.commit()
        flash(f'Dit {"pinned" if post.is_pinned else "unpinned"}.', 'success')
    return redirect(request.referrer or url_for('index'))

@app.route('/pin_comment/<int:comment_id>', methods=['POST'])
@login_required
def pin_comment(comment_id):
    comment = db.session.get(Comment, comment_id)
    if comment and comment.post.author == current_user:
        if not comment.is_pinned:
            Comment.query.filter_by(post_id=comment.post_id, is_pinned=True).update({'is_pinned': False})
        comment.is_pinned = not comment.is_pinned
        db.session.commit()
        flash(f'Comment {"pinned" if comment.is_pinned else "unpinned"}.', 'success')
    return redirect(request.referrer or url_for('index'))

@app.route('/report', methods=['GET', 'POST'])
@login_required
def report():
    target_type = request.args.get('target_type') or request.form.get('target_type')
    target_id = request.args.get('target_id') or request.form.get('target_id')
    
    if request.method == 'POST':
        reason = request.form.get('reason')
        if not reason or not target_type or not target_id:
            flash('Invalid report submitted.', 'danger')
            return redirect(url_for('index'))
            
        r = Report(reporter_id=current_user.id, target_type=target_type, reason=reason)
        if target_type == 'user':
            r.target_user_id = target_id
        elif target_type == 'post':
            r.post_id = target_id
        elif target_type == 'comment':
            r.comment_id = target_id
            
        db.session.add(r)
        db.session.commit()
        flash('Report submitted successfully. Thank you for keeping Ditter safe.', 'success')
        return redirect(url_for('index'))
        
    return render_template('report.html', target_type=target_type, target_id=target_id)

from functools import wraps

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
            flash('Admin access strictly required.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin')
@admin_required
def admin_dashboard():
    active_tab = request.args.get('tab', 'stats')
    active_filter = request.args.get('filter')
    
    total_users = User.query.count()
    total_posts = Post.query.count()
    pending_reports = Report.query.filter_by(status='pending').order_by(Report.timestamp.desc()).all()
    
    # Base query for users
    user_query = User.query
    if active_filter == 'banned':
        user_query = user_query.filter_by(is_banned=True)
    elif active_filter == 'restricted':
        users = user_query.all()
        all_users = [u for u in users if u.is_temporarily_restricted and not u.is_banned]
    else:
        all_users = user_query.order_by(User.join_date.desc()).all()
    
    if active_filter != 'restricted':
        if active_filter == 'banned':
            all_users = user_query.order_by(User.id.desc()).all()
        else:
            all_users = user_query.order_by(User.join_date.desc()).all()

    contact_messages = ContactMessage.query.order_by(ContactMessage.timestamp.desc()).all()
    banned_users_count = User.query.filter_by(is_banned=True).count()
    restricted_users_count = len([u for u in User.query.filter_by(is_banned=False).all() if u.is_temporarily_restricted])
    
    return render_template('admin.html', 
                           total_users=total_users, 
                           total_posts=total_posts, 
                           pending_reports=pending_reports, 
                           all_users=all_users,
                           contact_messages=contact_messages,
                           banned_users_count=banned_users_count,
                           restricted_users_count=restricted_users_count,
                           active_tab=active_tab,
                           active_filter=active_filter)

@app.route('/admin/user/<int:user_id>/toggle/<status_type>', methods=['POST'])
@admin_required
def admin_toggle_status(user_id, status_type):
    user = db.session.get(User, user_id)
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    if user == current_user:
        flash('You cannot modify your own status.', 'warning')
        return redirect(url_for('admin_dashboard'))

    if status_type == 'admin':
        user.is_admin = not user.is_admin
        print(f"DEBUG: Toggled admin for {user.username} to {user.is_admin}")
        flash(f'User @{user.username} admin status toggled.', 'success')
    elif status_type == 'ban':
        if user.is_currently_banned:
            print(f"DEBUG: Unbanning/Unrestricting {user.username}. Was: is_banned={user.is_banned}, banned_until={user.banned_until}")
            user.is_banned = False
            user.banned_until = None
            flash(f'User @{user.username} is now standard again.', 'success')
        else:
            print(f"DEBUG: Banning {user.username}")
            user.is_banned = True
            flash(f'User @{user.username} permanently banned.', 'danger')
    
    try:
        db.session.commit()
        print(f"DEBUG: Committed changes for {user.username}")
    except Exception as e:
        db.session.rollback()
        print(f"DEBUG: Error committing changes: {e}")
        flash('Internal error updating user status.', 'danger')
        
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/user/<int:user_id>/unrestrict', methods=['POST'])
@admin_required
def admin_unrestrict_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('User not found.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    if user.is_temporarily_restricted:
        user.banned_until = None
        user.is_banned = False
        db.session.commit()
        flash(f'User @{user.username} is now unrestricted.', 'success')
    else:
        flash('User is not restricted.', 'warning')
        
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/contact/<int:msg_id>/<action>', methods=['POST'])
@admin_required
def admin_handle_contact(msg_id, action):
    msg = db.session.get(ContactMessage, msg_id)
    if msg:
        if action == 'read':
            msg.is_read = True
        elif action == 'unread':
            msg.is_read = False
        elif action == 'delete':
            db.session.delete(msg)
        db.session.commit()
        flash('Message status updated.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/report/<int:report_id>/<action>', methods=['POST'])
@admin_required
def admin_handle_report(report_id, action):
    report = db.session.get(Report, report_id)
    if report:
        if action == 'dismiss':
            report.status = 'dismissed'
        elif action == 'delete_content':
            restriction = request.form.get('restriction')
            report.status = 'resolved'
            
            user_to_restrict = getattr(report, 'target_user', None)
            if not user_to_restrict:
                if report.target_type == 'post' and report.post:
                    user_to_restrict = report.post.author
                elif report.target_type == 'comment' and report.comment:
                    user_to_restrict = report.comment.author
            
            if user_to_restrict and restriction and restriction != 'none':
                from datetime import datetime, timedelta
                if restriction == '7':
                    user_to_restrict.banned_until = datetime.utcnow() + timedelta(days=7)
                elif restriction == '15':
                    user_to_restrict.banned_until = datetime.utcnow() + timedelta(days=15)
                elif restriction == 'ban':
                    user_to_restrict.is_banned = True
            
            if report.target_type == 'post' and report.post:
                post_to_del = report.post
                from models import Bookmark, Notification, likes, comment_likes
                Bookmark.query.filter_by(post_id=post_to_del.id).delete()
                Notification.query.filter_by(post_id=post_to_del.id).delete()
                db.session.execute(likes.delete().where(likes.c.post_id == post_to_del.id))
                Report.query.filter_by(target_type='post', post_id=post_to_del.id).delete()
                for c in post_to_del.comments:
                    db.session.execute(comment_likes.delete().where(comment_likes.c.comment_id == c.id))
                    Notification.query.filter_by(comment_id=c.id).delete()
                    Report.query.filter_by(target_type='comment', comment_id=c.id).delete()
                    db.session.delete(c)
                db.session.delete(post_to_del)
            elif report.target_type == 'comment' and report.comment:
                comment_to_del = report.comment
                from models import Notification, comment_likes
                db.session.execute(comment_likes.delete().where(comment_likes.c.comment_id == comment_to_del.id))
                Notification.query.filter_by(comment_id=comment_to_del.id).delete()
                Report.query.filter_by(target_type='comment', comment_id=comment_to_del.id).delete()
                db.session.delete(comment_to_del)
        elif action == 'ban_user':
            report.status = 'resolved'
            user_to_ban = getattr(report, 'target_user', None)
            if report.target_type == 'user' and user_to_ban:
                user_to_ban.is_banned = True
        db.session.commit()
        flash(f'Report #{report.id} acted upon: {action}.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        subject = request.form.get('subject')
        message = request.form.get('message')
        if not name or not email or not message:
            flash('Please fill out all required fields.', 'danger')
            return redirect(url_for('contact'))
            
        cm = ContactMessage(name=name, email=email, subject=subject, message=message)
        if current_user.is_authenticated:
            cm.user_id = current_user.id
        db.session.add(cm)
        db.session.commit()
        flash('Your message has been dispatched to our administrators.', 'success')
        return redirect(url_for('index'))
    return render_template('contact.html')

# --- Database Initialization ---
with app.app_context():
    db.create_all()
    try:
        from sqlalchemy import text
        db.session.execute(text("ALTER TABLE user ADD COLUMN banned_until DATETIME"))
        db.session.commit()
    except Exception:
        db.session.rollback()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
