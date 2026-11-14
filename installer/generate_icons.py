import os
from PIL import Image, ImageDraw

def create_omnistream_icon():
    size = 256
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Outer rounded squircle background
    # Dark modern emerald/slate
    bg_color = (13, 16, 14, 255)
    border_color = (42, 50, 44, 255)
    
    # Draw rounded rectangle
    draw.rounded_rectangle([8, 8, 248, 248], radius=56, fill=bg_color, outline=border_color, width=4)

    # Draw inner luminous glowing ambient circle
    for r in range(70, 20, -5):
        alpha = int(25 * (1.0 - (r - 20) / 50))
        draw.ellipse([128 - r, 128 - r, 128 + r, 128 + r], fill=(195, 244, 120, alpha))

    # Dynamic Modern Stream & Download Chevron Mark
    # Primary accent: OmniStream Lime #c3f478 (195, 244, 120)
    # Secondary accent: #86efac (134, 239, 172)
    lime = (195, 244, 120, 255)
    cyan = (56, 189, 248, 255)

    # Horizontal streaming stream waves (upper and lower arcs)
    # Stream curve 1 (Top)
    draw.arc([52, 60, 204, 150], start=190, end=350, fill=cyan, width=12)
    
    # Download Down-Arrow / Stream chevron in center
    arrow_color = lime
    # Vertical shaft
    draw.rounded_rectangle([118, 76, 138, 146], radius=6, fill=arrow_color)
    # Triangle head pointing down
    draw.polygon([(86, 134), (170, 134), (128, 178)], fill=arrow_color)
    
    # Bottom receiving cradle / horizon line
    draw.arc([68, 140, 188, 204], start=20, end=160, fill=lime, width=12)
    # Little side nodes
    draw.ellipse([64, 166, 76, 178], fill=cyan)
    draw.ellipse([180, 166, 192, 178], fill=lime)

    # Save PNG & ICO
    os.makedirs('static', exist_ok=True)
    img.save('static/icon.png', format='PNG')
    img.save('icon.png', format='PNG')
    
    # Multi-resolution ICO
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save('static/icon.ico', format='ICO', sizes=ico_sizes)
    img.save('icon.ico', format='ICO', sizes=ico_sizes)
    img.save('static/favicon.ico', format='ICO', sizes=[(16, 16), (32, 32), (48, 48)])
    print("Generated static/icon.png, icon.ico, static/icon.ico, static/favicon.ico successfully!")

if __name__ == '__main__':
    create_omnistream_icon()
