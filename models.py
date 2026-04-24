from datetime import datetime, timedelta
from collections import Counter
import re
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# Association table for followers
followers = db.Table('followers',
    db.Column('follower_id', db.Integer, db.ForeignKey('user.id')),
    db.Column('followed_id', db.Integer, db.ForeignKey('user.id'))
)

# Association table for follow requests
follow_requests = db.Table('follow_requests',
    db.Column('requester_id', db.Integer, db.ForeignKey('user.id')),
    db.Column('requested_id', db.Integer, db.ForeignKey('user.id'))
)

# Association table for likes
likes = db.Table('likes',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id')),
    db.Column('post_id', db.Integer, db.ForeignKey('post.id'))
)

# Association table for comment likes
comment_likes = db.Table('comment_likes',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id')),
    db.Column('comment_id', db.Integer, db.ForeignKey('comment.id'))
)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, unique=True, nullable=False)
    email = db.Column(db.String(120), index=True, unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    bio = db.Column(db.String(140))
    is_private = db.Column(db.Boolean, default=False)
    message_privacy = db.Column(db.String(20), default='everyone')
    join_date = db.Column(db.DateTime, default=datetime.utcnow)
    character_filename = db.Column(db.String(120), nullable=True)
    cover_filename = db.Column(db.String(120), nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    is_banned = db.Column(db.Boolean, default=False)
    banned_until = db.Column(db.DateTime, nullable=True)
    
    @property
    def is_currently_banned(self):
        """Checks if the user is either permanently banned or temporarily restricted."""
        if self.is_banned:
            return True
        if self.banned_until and self.banned_until > datetime.utcnow():
            return True
        return False

    @property
    def is_permanently_banned(self):
        """User is permanently locked out (is_banned=True)."""
        return self.is_banned

    @property
    def is_temporarily_restricted(self):
        """User is temporarily blocked from activity but can still log in."""
        if not self.is_banned and self.banned_until and self.banned_until > datetime.utcnow():
            return True
        return False
        
    # Relationships
    posts = db.relationship('Post', backref='author', lazy='dynamic')
    
    followed = db.relationship(
        'User', secondary=followers,
        primaryjoin=(followers.c.follower_id == id),
        secondaryjoin=(followers.c.followed_id == id),
        backref=db.backref('followers', lazy='dynamic'), lazy='dynamic')
        
    requested_to_follow = db.relationship(
        'User', secondary=follow_requests,
        primaryjoin=(follow_requests.c.requester_id == id),
        secondaryjoin=(follow_requests.c.requested_id == id),
        backref=db.backref('pending_requests', lazy='dynamic'), lazy='dynamic')
        
    liked_posts = db.relationship(
        'Post', secondary=likes,
        backref=db.backref('liked_by', lazy='dynamic'), lazy='dynamic')

    liked_comments = db.relationship(
        'Comment', secondary=comment_likes,
        backref=db.backref('liked_by', lazy='dynamic'), lazy='dynamic')

    # Messaging & Bookmarks
    bookmarks = db.relationship('Bookmark', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    messages_sent = db.relationship('Message', foreign_keys='Message.sender_id', backref='sender', lazy='dynamic')
    messages_received = db.relationship('Message', foreign_keys='Message.recipient_id', backref='recipient', lazy='dynamic')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
        
    def follow(self, user):
        if not self.is_following(user):
            self.followed.append(user)

    def unfollow(self, user):
        if self.is_following(user):
            self.followed.remove(user)
            
    def request_follow(self, user):
        if not self.has_requested_follow(user):
            self.requested_to_follow.append(user)
            
    def cancel_request(self, user):
        if self.has_requested_follow(user):
            self.requested_to_follow.remove(user)
            
    def has_requested_follow(self, user):
        return self.requested_to_follow.filter(
            follow_requests.c.requested_id == user.id).count() > 0

    def is_following(self, user):
        return self.followed.filter(
            followers.c.followed_id == user.id).count() > 0

    def get_suggestions(self, limit=3):
        return User.query.filter(User.id != self.id).filter(~User.followers.any(User.id == self.id)).limit(limit).all()

    def voted_in_poll(self, poll_id):
        from models import PollVote
        return PollVote.query.filter_by(user_id=self.id, poll_id=poll_id).count() > 0

    def get_poll_vote(self, poll_id):
        from models import PollVote
        return PollVote.query.filter_by(user_id=self.id, poll_id=poll_id).first()

    def like(self, post):
        if not self.has_liked(post):
            self.liked_posts.append(post)

    def unlike(self, post):
        if self.has_liked(post):
            self.liked_posts.remove(post)

    def has_liked(self, post):
        return self.liked_posts.filter(
            likes.c.post_id == post.id).count() > 0
            
    def has_reditted(self, post):
        return Post.query.filter_by(user_id=self.id, original_post_id=post.id).count() > 0

    def like_comment(self, comment):
        if not self.has_liked_comment(comment):
            self.liked_comments.append(comment)

    def unlike_comment(self, comment):
        if self.has_liked_comment(comment):
            self.liked_comments.remove(comment)

    def has_liked_comment(self, comment):
        return self.liked_comments.filter(
            comment_likes.c.comment_id == comment.id).count() > 0

    def has_bookmarked(self, post):
        from models import Bookmark
        return Bookmark.query.filter_by(user_id=self.id, post_id=post.id).count() > 0

    def bookmark(self, post):
        from models import Bookmark
        if not self.has_bookmarked(post):
            b = Bookmark(user_id=self.id, post_id=post.id)
            db.session.add(b)

    def unbookmark(self, post):
        from models import Bookmark
        b = Bookmark.query.filter_by(user_id=self.id, post_id=post.id).first()
        if b:
            db.session.delete(b)


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.String(280), nullable=True) # Note: content can be null if it's just a pure redit without extra text, though for simplicity we default to not required if it's a redit.
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    original_post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=True)
    image_filename = db.Column(db.String(120), nullable=True) # Kept for legacy compatibility
    is_pinned = db.Column(db.Boolean, default=False)
    
    # Relationships for redits, comments, and polls
    original_post = db.relationship('Post', remote_side=[id], backref=db.backref('redits', lazy='dynamic'))
    comments = db.relationship('Comment', backref='post', lazy='dynamic')
    poll = db.relationship('Poll', backref='post', uselist=False, cascade='all, delete-orphan')
    images = db.relationship('PostImage', backref='post', lazy='dynamic', cascade='all, delete-orphan')

    @staticmethod
    def get_trending_hashtags(limit=5):
        recent_posts = Post.query.filter(Post.timestamp > datetime.utcnow() - timedelta(days=7)).limit(500).all()
        hashtag_re = re.compile(r'#(\w+)')
        hashtags = []
        for post in recent_posts:
            if post.content:
                tags = hashtag_re.findall(post.content)
                hashtags.extend([tag.lower() for tag in tags])
        return Counter(hashtags).most_common(limit)

class PostImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    filename = db.Column(db.String(120), nullable=False)

class Poll(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    question = db.Column(db.String(280), nullable=True)
    options = db.relationship('PollOption', backref='poll', lazy='dynamic', cascade='all, delete-orphan')

class PollOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey('poll.id'), nullable=False)
    text = db.Column(db.String(100), nullable=False)
    is_correct = db.Column(db.Boolean, default=False)
    votes = db.relationship('PollVote', backref='option', lazy='dynamic', cascade='all, delete-orphan')

class PollVote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    poll_id = db.Column(db.Integer, db.ForeignKey('poll.id'), nullable=False)
    option_id = db.Column(db.Integer, db.ForeignKey('poll_option.id'), nullable=False)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'poll_id', name='_user_poll_uc'),)
    
class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.String(280), nullable=False)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    is_pinned = db.Column(db.Boolean, default=False)
    
    author = db.relationship('User', backref=db.backref('comments', lazy='dynamic'))

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True, nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    notification_type = db.Column(db.String(20), nullable=False) # 'like', 'comment_like', 'comment', 'redit', 'quote', 'follow', 'request', 'approve'
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=True)
    comment_id = db.Column(db.Integer, db.ForeignKey('comment.id'), nullable=True)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)
    
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('notifications', lazy='dynamic', cascade='all, delete-orphan'))
    sender = db.relationship('User', foreign_keys=[sender_id])
    post = db.relationship('Post')
    comment = db.relationship('Comment')

class Bookmark(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)

    post = db.relationship('Post')

    __table_args__ = (db.UniqueConstraint('user_id', 'post_id', name='_user_bookmark_uc'),)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    body = db.Column(db.String(500), nullable=False)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)
    is_request = db.Column(db.Boolean, default=True)
    is_accepted = db.Column(db.Boolean, default=False)
    images = db.relationship('MessageImage', backref='message', lazy='dynamic', cascade='all, delete-orphan')

class MessageImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=False)
    filename = db.Column(db.String(120), nullable=False)

class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    target_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=True)
    comment_id = db.Column(db.Integer, db.ForeignKey('comment.id'), nullable=True)
    target_type = db.Column(db.String(20), nullable=False) # 'user', 'post', 'comment'
    reason = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(20), default='pending') # 'pending', 'resolved', 'dismissed'
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)

    reporter = db.relationship('User', foreign_keys=[reporter_id])
    target_user = db.relationship('User', foreign_keys=[target_user_id])
    post = db.relationship('Post')
    comment = db.relationship('Comment')

class ContactMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    name = db.Column(db.String(64), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    message = db.Column(db.String(1000), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)

    user = db.relationship('User')

