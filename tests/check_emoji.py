#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检查Python文件中的表情符号和非ASCII字符
"""

import os
import re
import sys
from pathlib import Path

def find_emojis_in_file(file_path):
    """检查单个文件中的表情符号"""
    emoji_pattern = re.compile(
        r'[\U0001F300-\U0001F9FF]'  # 常见表情符号范围
        r'|[\U0001F600-\U0001F64F]'  # 表情符号
        r'|[\U00002600-\U000026FF]'  # 杂项符号
        r'|[\U00002700-\U000027BF]'  # 装饰符号
        r'|[\U0001F680-\U0001F6FF]'  # 交通和地图符号
        r'|[\U0001F900-\U0001F9FF]'  # 补充表情符号
    )
    
    # 匹配Unicode转义形式：\U0001F680
    unicode_escape_pattern = re.compile(r'\\U[0-9a-fA-F]{8}')
    
    # 匹配非ASCII字符（包括中文）
    non_ascii_pattern = re.compile(r'[^\x00-\x7f]')
    
    emoji_lines = []
    unicode_escape_lines = []
    non_ascii_lines = []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f, 1):
                # 检查表情符号字符
                emoji_matches = emoji_pattern.findall(line)
                if emoji_matches:
                    emoji_lines.append({
                        'line': i,
                        'content': line.rstrip('\n'),
                        'matches': emoji_matches
                    })
                
                # 检查Unicode转义序列
                unicode_matches = unicode_escape_pattern.findall(line)
                if unicode_matches:
                    unicode_escape_lines.append({
                        'line': i,
                        'content': line.rstrip('\n'),
                        'matches': unicode_matches
                    })
                
                # 检查非ASCII字符
                if non_ascii_pattern.search(line):
                    # 过滤掉中文字符（中文是正常的）
                    chinese_chars = re.findall(r'[\u4e00-\u9fff]', line)
                    other_non_ascii = [
                        c for c in line 
                        if ord(c) > 127 and c not in chinese_chars
                    ]
                    if other_non_ascii:
                        non_ascii_lines.append({
                            'line': i,
                            'content': line.rstrip('\n'),
                            'matches': other_non_ascii
                        })
    except UnicodeDecodeError:
        # 尝试其他编码
        try:
            with open(file_path, 'r', encoding='gbk') as f:
                content = f.read()
                print(f"警告: {file_path} 使用GBK编码")
        except:
            print(f"无法读取文件: {file_path}")
    
    return {
        'emojis': emoji_lines,
        'unicode_escapes': unicode_escape_lines,
        'other_non_ascii': non_ascii_lines
    }

def scan_project(project_path, extensions=('.py', '.txt', '.md', '.json', '.yaml', '.yml')):
    """扫描整个项目"""
    project_path = Path(project_path)
    results = {}
    
    for ext in extensions:
        for file_path in project_path.rglob(f'*{ext}'):
            if any(part.startswith('.') or part.startswith('__') for part in file_path.parts):
                continue
                
            file_results = find_emojis_in_file(file_path)
            if (file_results['emojis'] or 
                file_results['unicode_escapes'] or 
                file_results['other_non_ascii']):
                
                results[str(file_path)] = file_results
    
    return results

def print_results(results):
    """打印检查结果"""
    if not results:
        print("✅ 未找到表情符号或非ASCII字符！")
        return
    
    print("=" * 80)
    print("检查结果：")
    print("=" * 80)
    
    for file_path, file_results in results.items():
        print(f"\n📁 文件: {file_path}")
        print("-" * 40)
        
        if file_results['emojis']:
            print("⚠️  表情符号字符:")
            for item in file_results['emojis']:
                print(f"   第 {item['line']} 行: {item['content']}")
                print(f"   发现字符: {', '.join(repr(c) for c in item['matches'])}")
        
        if file_results['unicode_escapes']:
            print("⚠️  Unicode转义序列:")
            for item in file_results['unicode_escapes']:
                print(f"   第 {item['line']} 行: {item['content']}")
                print(f"   发现转义: {', '.join(item['matches'])}")
        
        if file_results['other_non_ascii']:
            print("⚠️  其他非ASCII字符（非中文）:")
            for item in file_results['other_non_ascii']:
                print(f"   第 {item['line']} 行: {item['content']}")
                print(f"   发现字符: {', '.join(f'{c} (U+{ord(c):04X})' for c in item['matches'])}")

def main():
    """主函数"""
    if len(sys.argv) > 1:
        project_path = sys.argv[1]
    else:
        # 默认扫描当前目录
        project_path = "."
    
    print(f"正在扫描项目: {project_path}")
    print("正在检查表情符号、Unicode转义序列和其他非ASCII字符...")
    
    results = scan_project(project_path)
    
    if results:
        print_results(results)
        
        # 统计
        total_issues = sum(
            len(r['emojis']) + len(r['unicode_escapes']) + len(r['other_non_ascii'])
            for r in results.values()
        )
        print(f"\n{'='*80}")
        print(f"📊 统计: 在 {len(results)} 个文件中发现 {total_issues} 个问题")
        print(f"{'='*80}")
        
        # 询问是否要清理
        if input("\n是否要自动清理这些问题？(y/N): ").lower() == 'y':
            clean_files(results)
    else:
        print("✅ 未发现问题！")

def clean_files(results):
    """清理文件中的问题字符"""
    for file_path, file_results in results.items():
        print(f"\n正在清理: {file_path}")
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            modified = False
            for i in range(len(lines)):
                line = lines[i]
                
                # 替换表情符号
                emoji_pattern = re.compile(
                    r'[\U0001F300-\U0001F9FF]'
                    r'|[\U0001F600-\U0001F64F]'
                    r'|[\U00002600-\U000026FF]'
                    r'|[\U00002700-\U000027BF]'
                    r'|[\U0001F680-\U0001F6FF]'
                    r'|[\U0001F900-\U0001F9FF]'
                )
                new_line = emoji_pattern.sub('', line)
                
                # 替换Unicode转义序列
                unicode_pattern = re.compile(r'\\U[0-9a-fA-F]{8}')
                new_line = unicode_pattern.sub('', new_line)
                
                if new_line != line:
                    lines[i] = new_line
                    modified = True
            
            if modified:
                # 备份原文件
                backup_path = file_path + '.bak'
                import shutil
                shutil.copy2(file_path, backup_path)
                print(f"  已创建备份: {backup_path}")
                
                # 写入清理后的文件
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.writelines(lines)
                print("  ✅ 清理完成")
            else:
                print("  ⏭️ 无需清理")
                
        except Exception as e:
            print(f"  ❌ 清理失败: {e}")

if __name__ == '__main__':
    main()