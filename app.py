import os
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from minio import Minio
from io import BytesIO
import openai
from dotenv import load_dotenv
from urllib.parse import urljoin

load_dotenv()

app = Flask(__name__)
app.secret_key = 'some_secret_key'  # 请使用更安全的方式管理密钥

# 数据库配置
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///data.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# OpenAI配置
openai.api_key = os.getenv("OPENAI_API_KEY")
openai.api_base = os.getenv("OPENAI_API_BASE")

# MinIO配置
minio_client = Minio(
    endpoint=os.getenv("MINIO_ENDPOINT", "127.0.0.1:9000"),
    access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
    secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
    secure=False
)
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "ojousama-bucket")
# 创建bucket如果不存在
found = minio_client.bucket_exists(MINIO_BUCKET)
if not found:
    minio_client.make_bucket(MINIO_BUCKET)


# 数据模型
class Ojou(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)  # 人设描述
    quote = db.Column(db.Text, nullable=True)         # 语录
    image_filename = db.Column(db.String(255), nullable=True)
    story_filename = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()


@app.route('/')
def index():
    ojous = Ojou.query.all()
    return render_template('index.html', ojous=ojous)


@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        quote = request.form.get('quote')
        image_file = request.files.get('image_file')
        pdf_file = request.files.get('pdf_file')

        # 创建御姐记录
        new_ojou = Ojou(name=name, description=description, quote=quote)
        db.session.add(new_ojou)
        db.session.commit()

        # 上传图片到Minio
        if image_file and image_file.filename:
            img_filename = f"{new_ojou.id}_image_{image_file.filename}"
            minio_client.put_object(MINIO_BUCKET, img_filename, image_file, length=-1, part_size=10*1024*1024)
            new_ojou.image_filename = img_filename

        # 上传PDF到Minio
        if pdf_file and pdf_file.filename:
            pdf_filename = f"{new_ojou.id}_story_{pdf_file.filename}"
            minio_client.put_object(MINIO_BUCKET, pdf_filename, pdf_file, length=-1, part_size=10*1024*1024)
            new_ojou.story_filename = pdf_filename

        db.session.commit()
        flash("御姐资料上传成功！", "success")
        return redirect(url_for('index'))

    return render_template('upload.html')


@app.route('/ojou/<int:ojou_id>')
def detail(ojou_id):
    ojou = Ojou.query.get_or_404(ojou_id)
    # 生成Minio访问URL（需要配置Minio为公共访问或者使用预签名URL）
    image_url = None
    pdf_url = None

    if ojou.image_filename:
        # 获取临时url
        image_url = minio_client.presigned_get_object(MINIO_BUCKET, ojou.image_filename)

    if ojou.story_filename:
        pdf_url = minio_client.presigned_get_object(MINIO_BUCKET, ojou.story_filename)

    return render_template('detail.html', ojou=ojou, image_url=image_url, pdf_url=pdf_url)


@app.route('/chat/<int:ojou_id>', methods=['GET', 'POST'])
def chat(ojou_id):
    ojou = Ojou.query.get_or_404(ojou_id)
    # 基于御姐人设描述及语录作为系统提示词
    base_prompt = f"你是一位名叫{ojou.name}的御姐，有如下人设描述:\n\n{ojou.description}\n\n" \
                  f"你的经典语录：{ojou.quote if ojou.quote else ''}\n\n" \
                  "请以御姐的身份来回答下面用户的问题。"
    
    if request.method == 'POST':
        user_msg = request.form.get('user_input')
        if user_msg:
            # 调用OpenAI GPT接口
            response = openai.ChatCompletion.create(
                model=os.getenv("CHAT_MODEL","qwen2:0.5b"),
                messages=[
                    {"role": "system", "content": base_prompt},
                    {"role": "user", "content": user_msg}
                ]
            )
            answer = response.choices[0].message.content.strip()
            return render_template('chat.html', ojou=ojou, conversation=[("user", user_msg), ("ojou", answer)])

    # 初次打开聊天页面时没有对话
    return render_template('chat.html', ojou=ojou, conversation=[])


if __name__ == '__main__':
    app.run(debug=True,host="0.0.0.0")
