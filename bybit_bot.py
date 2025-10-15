#!/usr/bin/env python3

import asyncio
import json
import logging
import os
import signal
import sys
import websockets
import aiohttp
from datetime import datetime
from telegram import Bot
from telegram.error import TelegramError
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class BybitLiquidationBot:
    def __init__(self):
        load_dotenv()
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.min_size = float(os.getenv('MIN_LIQUIDATION_SIZE', '1000'))
        
        if not self.bot_token or not self.chat_id:
            logger.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variables")
            sys.exit(1)
        self.telegram_bot = Bot(token=self.bot_token)
        self.ws = None
        self.running = False
        
        self.bybit_ws_url = "wss://stream.bybit.com/v5/public/linear"
        self.bybit_api_url = "https://api.bybit.com/v5/market/instruments-info"
    
    async def send_telegram_message(self, message):
        try:
            await self.telegram_bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='HTML'
            )
            logger.info("Message sent to Telegram")
        except TelegramError as e:
            logger.error(f"Failed to send Telegram message: {e}")
    
    async def send_startup_message(self):
        symbols = await self.get_all_futures_symbols()
        symbol_count = len(symbols)
        
        message = f"""
🤖 <b>Bybit Liquidation Bot Started</b>

✅ Monitoring {symbol_count} futures symbols
📊 Minimum size: ${self.min_size:,.0f}
⚡ Real-time monitoring
📈 Covers: Linear futures

Bot is now running...
        """.strip()
        await self.send_telegram_message(message)
    
    async def format_liquidation_alert(self, data):
        symbol = data.get('symbol', 'Unknown')
        side = data.get('side', 'Unknown')
        size = data.get('size', '0')
        price = data.get('price', '0')
        amount = data.get('amount', '0')
        time = data.get('time', 'Unknown')
        
        side_emoji = "🔴" if side == "Sell" else "🟢"
        
        message = f"""
🚨 <b>LIQUIDATION ALERT</b> 🚨

{side_emoji} <b>{symbol}</b> {side} Liquidation
💰 Size: {size}
💸 Amount: ${amount}
💵 Price: ${price}
⏰ Time: {time}

#Liquidation #{symbol.replace('USDT', '')}
        """.strip()
        
        return message
    
    async def get_all_futures_symbols(self):
        try:
            symbols = []
            categories = ['linear']
            
            async with aiohttp.ClientSession() as session:
                for category in categories:
                    params = {
                        'category': category,
                        'limit': 1000
                    }
                    
                    async with session.get(self.bybit_api_url, params=params) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data.get('retCode') == 0:
                                for item in data.get('result', {}).get('list', []):
                                    symbol = item.get('symbol')
                                    if symbol:
                                        symbols.append(f"allLiquidation.{symbol}")
                                logger.info(f"Found {len([s for s in symbols if category in s])} {category} symbols")
                            else:
                                logger.error(f"API error for {category}: {data.get('retMsg')}")
                        else:
                            logger.error(f"HTTP error for {category}: {response.status}")
            
            logger.info(f"Total symbols to monitor: {len(symbols)}")
            return symbols
            
        except Exception as e:
            logger.error(f"Failed to fetch symbols: {e}")
            fallback_symbols = [
                "allLiquidation.BTCUSDT", "allLiquidation.ETHUSDT", "allLiquidation.ADAUSDT",
                "allLiquidation.BNBUSDT", "allLiquidation.SOLUSDT", "allLiquidation.XRPUSDT",
                "allLiquidation.DOTUSDT", "allLiquidation.DOGEUSDT", "allLiquidation.AVAXUSDT",
                "allLiquidation.MATICUSDT"
            ]
            logger.info(f"Using fallback symbols: {len(fallback_symbols)}")
            return fallback_symbols
    
    async def connect_to_bybit(self):
        try:
            self.ws = await websockets.connect(self.bybit_ws_url)
            logger.info("Connected to Bybit WebSocket")
            
            symbols = await self.get_all_futures_symbols()
            
            if symbols:
                subscribe_msg = {"op": "subscribe", "args": symbols}
                await self.ws.send(json.dumps(subscribe_msg))
                logger.info(f"Subscribed to {len(symbols)} liquidation topics")
            else:
                logger.error("No symbols found to subscribe to")
                return False
            
            return True
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            await self.send_telegram_message(f"❌ Connection failed: {str(e)}")
            return False
    
    async def listen_for_liquidations(self):
        if not self.ws:
            return
        
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                    
                    if data.get('topic') and data.get('topic').startswith('allLiquidation'):
                        await self.process_liquidation(data.get('data', {}))
                        
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse message: {e}")
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    
        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed")
            await self.send_telegram_message("⚠️ WebSocket connection lost")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            await self.send_telegram_message(f"❌ WebSocket error: {str(e)}")
    
    async def process_liquidation(self, liquidation_data):
        if not liquidation_data:
            return
        for liquidation in liquidation_data:
            try:
                symbol = liquidation.get('s', '')
                side = liquidation.get('S', '')
                size = float(liquidation.get('v', '0'))
                price = liquidation.get('p', '0')
                
                liquidation_value = size * float(price)
                if liquidation_value >= self.min_size:
                    timestamp = liquidation.get('T', '')
                    if timestamp:
                        try:
                            dt = datetime.fromtimestamp(int(timestamp) / 1000)
                            formatted_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                        except:
                            formatted_time = timestamp
                    else:
                        formatted_time = "Unknown"
                    notification_data = {
                        'symbol': symbol,
                        'side': side,
                        'size': f"{size:,.2f}",
                        'price': f"{float(price):,.2f}",
                        'amount': f"{size * float(price):,.2f}",
                        'time': formatted_time
                    }
                    
                    message = await self.format_liquidation_alert(notification_data)
                    await self.send_telegram_message(message)
                    
                    logger.info(f"Alert sent: {symbol} {side} ${liquidation_value:,.2f}")
                
            except Exception as e:
                print(liquidation)
                logger.error(f"Error processing liquidation: {e}")
    
    async def start_monitoring(self):
        self.running = True
        logger.info("Starting Bybit Liquidation Bot...")
        
        await self.send_startup_message()
        
        while self.running:
            try:
                if await self.connect_to_bybit():
                    await self.listen_for_liquidations()
                if self.running:
                    logger.info("Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)
                    
            except KeyboardInterrupt:
                logger.info("Received interrupt signal")
                break
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                if self.running:
                    await asyncio.sleep(5)
    
    async def stop(self):
        self.running = False
        if self.ws:
            await self.ws.close()
        logger.info("Bot stopped")

async def main():
    bot = BybitLiquidationBot()
    
    try:
        await bot.start_monitoring()
    except KeyboardInterrupt:
        logger.info("Bot interrupted by user")
    finally:
        await bot.stop()

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}")
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
