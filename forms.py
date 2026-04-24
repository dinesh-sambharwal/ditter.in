from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, TextAreaField, BooleanField, SelectField, MultipleFileField, IntegerField
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError, Optional
from flask_wtf.file import FileAllowed
from models import User

class RegistrationForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=20)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Sign Up')

    def validate_username(self, username):
        user = User.query.filter_by(username=username.data).first()
        if user:
            raise ValidationError('That username is already taken. Please choose a different one.')

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user:
            raise ValidationError('That email is already registered. Please choose a different one.')

class LoginForm(FlaskForm):
    username_or_email = StringField('Username or Email', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

class ResetPasswordRequestForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Request Password Reset')

class ResetPasswordForm(FlaskForm):
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Reset Password')

from flask_wtf.file import FileField

class PostForm(FlaskForm):
    content = TextAreaField('What is happening?!', validators=[Optional(), Length(max=280)])
    images = MultipleFileField('Attach Images (Optional)', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg', 'gif', 'webp'], 'Images only!')])
    
    # Poll fields
    poll_question = StringField('Poll Question', validators=[Optional(), Length(max=140)])
    poll_option1 = StringField('Option 1', validators=[Optional(), Length(max=100)])
    poll_option2 = StringField('Option 2', validators=[Optional(), Length(max=100)])
    poll_option3 = StringField('Option 3', validators=[Optional(), Length(max=100)])
    poll_option4 = StringField('Option 4', validators=[Optional(), Length(max=100)])
    poll_option5 = StringField('Option 5', validators=[Optional(), Length(max=100)])
    poll_option6 = StringField('Option 6', validators=[Optional(), Length(max=100)])
    poll_correct_option = IntegerField('Correct Option Index', validators=[Optional()])
    
    submit = SubmitField('Post')

    def validate_content(self, field):
        # We check both the text content and if any files were uploaded
        if not self.content.data and (not self.images.data or (isinstance(self.images.data, list) and len(self.images.data) == 0) or (hasattr(self.images.data, 'filename') and not self.images.data.filename)):
            raise ValidationError('You must provide text or at least one image.')

class EditProfileForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    email = StringField('Email', validators=[DataRequired(), Email()])
    bio = TextAreaField('About me', validators=[Length(min=0, max=140)])
    is_private = BooleanField('Private Account (Only approved followers can see your dits)')
    message_privacy = SelectField('Who can message you?', choices=[
        ('everyone', 'Everyone'),
        ('followed', 'People I follow'),
        ('none', 'No one')
    ], default='everyone')
    character = FileField('Upload Profile Character', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg', 'gif', 'webp'], 'Images only!')])
    cover = FileField('Upload Cover Photo', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg', 'gif', 'webp'], 'Images only!')])
    chosen_default = StringField('', validators=[Optional()])
    chosen_cover = StringField('', validators=[Optional()])
    current_password = PasswordField('Current Password (Required only if changing password)', validators=[Optional()])
    new_password = PasswordField('New Password', validators=[Optional(), Length(min=6)])
    confirm_new_password = PasswordField('Confirm New Password', validators=[EqualTo('new_password', message='Passwords must match.')])
    submit = SubmitField('Submit')

    def __init__(self, original_username, original_email, *args, **kwargs):
        super(EditProfileForm, self).__init__(*args, **kwargs)
        self.original_username = original_username
        self.original_email = original_email

    def validate_username(self, username):
        if username.data != self.original_username:
            user = User.query.filter_by(username=username.data).first()
            if user:
                raise ValidationError('Please use a different username.')
                
    def validate_email(self, email):
        if email.data != self.original_email:
            user = User.query.filter_by(email=email.data).first()
            if user:
                raise ValidationError('Please use a different email.')

class CommentForm(FlaskForm):
    body = TextAreaField('Reply to this Dit...', validators=[DataRequired(), Length(max=280)])
    submit = SubmitField('Reply')

class MessageForm(FlaskForm):
    body = TextAreaField('Message', validators=[Optional(), Length(max=500)])
    images = MultipleFileField('Attach Images (Optional)', validators=[Optional(), FileAllowed(['jpg', 'png', 'jpeg', 'gif', 'webp'], 'Images only!')])
    submit = SubmitField('Send')
