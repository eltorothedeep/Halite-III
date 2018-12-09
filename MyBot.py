#!/usr/bin/env python3

# Import the Halite SDK, which will let you interact with the game.
import hlt
from hlt import constants

import random
import logging
import copy
from enum import IntEnum, auto

# v1  base bot 
# v2  better movement
# v3  goal based movement
# v4  stuck resolution
# v5  Fib based ship cost, no limit, no delay, return when closer to full
# v6  Homing at end, start with RETURNING, EXPLORE range increase over game after RETURNING, 
#     +1 range on EXPLORING, fix no move bug, strip mine
# v7  Third Fib for ship cost hike, stop shipbuilding when HOMING, HOMING sooner because more ships,
#     Dropoffs, progressively deeper extraction
# v8  Fix log crash, smaller create delay, lower dropoffoverhead
# v9  Dropoff pos range smaller, Pause every other frame when RETURNING, Fix return to storage bug, limit ship creation to first 80%
# v10 Two-third fib, extract more, 
# v11 must move if no halite
# v12 radial explore, don't pause if too little halite, limit to 1 dropoff for 32x32

class shipInfo(IntEnum):
    STATE = 0
    GOAL = auto()
    LASTPOS = auto()
    PAUSE = auto()
    DROPID = auto()
#

class shipState(IntEnum):
    RETURNING = auto()
    EXPLORING = auto()
    CONVERTING = auto()
    HOMING = auto()
#

class dropInfo(IntEnum):
    RADIAL = 0
#

useSaboteurs = False
homing_begun = False
dropoffthisframe = False
reservedfordropoff = 0
dropoffcostoverhead = 1.25
createshipturn = 0
radial = [[-1,-1],[0,-1],[1,-1],[1,0],[1,1],[0,1],[-1,1],[-1,0]]
ship_status = {}
dropoff_status = {}

def fibbing(n):
    if n == 0:
        return 0
    elif n==1 or n==2:
        return 1
        
    return fibbing(n-1)+fibbing(n-2)
#

def GetFib(fn, arg):
    fibs = {}
    if arg not in fibs:
        fibs[arg] = fn(arg)
    return fibs[arg]
#

def GetShipBuildThreshold(num):
    return int(constants.SHIP_COST * (1+(GetFib(fibbing, num)/100))) + reservedfordropoff
#    

def IsAtEdgeOfMap( position, map ):
    if (position.x == 0 or position.x == map.width-1 or position.y == 0 or position.y == map.height-1 ):
        return True
    #
    return False
#

def GetRichestPosition( curPos, range, mustmove, avoidedges, map ):
    first = mustmove
    range *= 4
    best = curPos
    max = map[curPos].halite_amount
    adList = curPos.get_surrounding_cardinals()
    for adjacent in adList:
        if range > 0:
            for ad1 in adjacent.get_surrounding_cardinals():
                adList.append(map.normalize(ad1))
            #
            range -= 1
        #
        
        if map[adjacent].is_empty and ((first and map[adjacent].halite_amount >= max) or (map[adjacent].halite_amount > max)):
            adjacent = map.normalize(adjacent)
            if avoidedges:
                if not IsAtEdgeOfMap(ad1, map):
                    best = adjacent
                    max = map[adjacent].halite_amount
                    first = False
                #
            else:
                best = adjacent
                max = map[adjacent].halite_amount
                first = False
            #
        #
    #
    return best
#

def ConvertToDropoff(ship, me, costtillnow, map):
    if len(me.get_dropoffs()) == 1 and map.height == 32:
        return False, None
        
    if len(me.get_ships()) > (1+len(me.get_dropoffs()))*10 and me.halite_amount > constants.DROPOFF_COST + costtillnow:
        far_enough = map.calculate_distance(ship.position, me.shipyard.position) > map.height / 3
        for dropoff in me.get_dropoffs():
            far_enough = far_enough and (map.calculate_distance(ship.position, dropoff.position) > map.height / 3)
        #
        if far_enough:
            dropoffpos = GetRichestPosition( ship.position, 2, False, True, map )
            return True, dropoffpos
        #
    #
    return False, None    
#

def GetClosestStoragePosition(position, me, map):
    storageindex = 0
    min = map.calculate_distance(ship.position, me.shipyard.position)
    pos = me.shipyard.position
    for dropoff in me.get_dropoffs():
        dist = map.calculate_distance(ship.position, dropoff.position)
        if (dist <= min):
            min = dist
            pos = dropoff.position
            storageindex = dropoff.id
        #
    #
    return pos,storageindex
#

def GetRadialExplorePos( pos, dropid ):
    global radial
    global dropoff_status
    if dropid not in dropoff_status:
        dropoff_status[dropid] = 0
    #
    mul = 1+int(dropoff_status[dropid]/8)
    idx = dropoff_status[dropid] % 8
    offset = radial[idx]
    radpos = copy.deepcopy(pos)
    radpos.x += offset[0]*mul
    radpos.y += offset[1]*mul
    dropoff_status[dropid] += 1
    return radpos
#

# This game object contains the initial game state 
game = hlt.Game()
# Respond with your name.
game.ready("DeepCv11")

while True:
    # Get the latest game state.
    game.update_frame()
    # You extract player metadata and the updated map metadata here for convenience.
    me = game.me
    game_map = game.game_map
    
    extractionratio = 25 + ( int(game.turn_number/100) * 5 )
    min_halite = constants.MAX_HALITE / extractionratio
    
    # A command queue holds all the commands you will run this turn.
    command_queue = []
    
    numships = len(me.get_ships())
    numdropoffs = len(me.get_dropoffs())
    exploring = 0
    returning = 0
    for ship in me.get_ships():
        if ship.id not in ship_status:
            ship_status[ship.id] = [shipState.RETURNING, ship.position, ship.position, False, 0]
        elif ship_status[ship.id][shipInfo.STATE] == shipState.RETURNING:
            if ship.position == ship_status[ship.id][shipInfo.LASTPOS] and not ship_status[ship.id][shipInfo.PAUSE]: 
                ship_status[ship.id][shipInfo.STATE] = shipState.EXPLORING
                ship_status[ship.id][shipInfo.GOAL] = None
                exploring += 1
            else:
                ship_status[ship.id][shipInfo.PAUSE] = not ship_status[ship.id][shipInfo.PAUSE]
                returning += 1
            #
        elif ship_status[ship.id][shipInfo.STATE] == shipState.EXPLORING:
            exploring += 1
            if ship.position == ship_status[ship.id][shipInfo.LASTPOS]:
                ship_status[ship.id][shipInfo.GOAL] = None
            #
        #
        if ship_status[ship.id][shipInfo.STATE] != shipState.HOMING:
            turns_left = constants.MAX_TURNS - game.turn_number
            if turns_left < 100:
                storage, id = GetClosestStoragePosition(ship.position, me, game_map)
                distance = game_map.calculate_distance(ship.position, storage)
                if (distance * 2.5) > turns_left:
                    if ship_status[ship.id][shipInfo.STATE] == shipState.EXPLORING:
                        exploring -= 1
                    elif ship_status[ship.id][shipInfo.STATE] == shipState.RETURNING:
                        returning -= 1
                    elif ship_status[ship.id][shipInfo.STATE] == shipState.CONVERTING:
                        reservedfordropoff -= int(constants.DROPOFF_COST * dropoffcostoverhead)
                    #
                    ship_status[ship.id][shipInfo.STATE] = shipState.HOMING
                    ship_status[ship.id][shipInfo.GOAL] = storage
                    homing_begun = True
                    #logging.info("Ship {} HOMING Distance {}".format(ship.id, distance))
                #
            #
        #
        #logging.info("Ship {} state {} goal {} pos {} halite {}.".format(ship.id, str(ship_status[ship.id][shipInfo.STATE]), ship_status[ship.id][shipInfo.GOAL], ship.position, ship.halite_amount))
        ship_status[ship.id][shipInfo.LASTPOS] = ship.position
    #
    #logging.info("PrePro Complete")
    
    dropoffthisturn = False
    costthisturn = 0

    for ship in me.get_ships():        
        if ship_status[ship.id][shipInfo.STATE] == shipState.RETURNING:
            if ship.position == ship_status[ship.id][shipInfo.GOAL]:
                ship_status[ship.id][shipInfo.STATE] = shipState.EXPLORING
                if False:
                    best = GetRichestPosition( ship.position, int(game.turn_number/100), True, False, game_map )
                else:
                    best = game_map.normalize(GetRadialExplorePos(ship.position, ship_status[ship.id][shipInfo.DROPID]))
                #
                if game_map.calculate_distance(ship.position, best) > 1:
                    ship_status[ship.id][shipInfo.GOAL] = best
                else:
                    ship_status[ship.id][shipInfo.GOAL] = None
                #
                costthisturn += int(game_map[ship.position].halite_amount * 0.1)
                move = game_map.naive_navigate(ship, best)
                command_queue.append(ship.move(move))
                #logging.info("Ship {} Storage {} Goal {} Distance {} Move {}".format(ship.id, ship.position, best, game_map.calculate_distance(ship.position, best), move))
            else:
                if game_map[ship.position].halite_amount < int(min_halite/2) or not ship_status[ship.id][shipInfo.PAUSE]:
                    ship_status[ship.id][shipInfo.PAUSE] = False
                    costthisturn += int(game_map[ship.position].halite_amount * 0.1)
                    move = game_map.naive_navigate(ship, ship_status[ship.id][shipInfo.GOAL])
                    command_queue.append(ship.move(move))
                else:
                    command_queue.append(ship.stay_still())                #
            #
        elif ship_status[ship.id][shipInfo.STATE] == shipState.HOMING:
            if game_map.calculate_distance(ship.position, ship_status[ship.id][shipInfo.GOAL]) == 1:
                moves = game_map.get_unsafe_moves(ship.position, ship_status[ship.id][shipInfo.GOAL])
                command_queue.append(ship.move(moves[0]))
            else:
                costthisturn += int(game_map[ship.position].halite_amount * 0.1)
                move = game_map.naive_navigate(ship, ship_status[ship.id][shipInfo.GOAL])
                command_queue.append(ship.move(move))
            #
        elif ship_status[ship.id][shipInfo.STATE] == shipState.CONVERTING:
            if ship.position == ship_status[ship.id][shipInfo.GOAL]:
                command_queue.append(ship.make_dropoff())
                reservedfordropoff -= int(constants.DROPOFF_COST * dropoffcostoverhead)
            else:
                costthisturn += int(game_map[ship.position].halite_amount * 0.1)
                move = game_map.naive_navigate(ship, ship_status[ship.id][shipInfo.GOAL])
                command_queue.append(ship.move(move))
            #
        elif ship_status[ship.id][shipInfo.STATE] == shipState.EXPLORING:
            if ship.is_full:
                convert = False
                dropoffpos = ship.position
                if not dropoffthisturn:
                    convert, dropoffpos = ConvertToDropoff(ship, me, costthisturn, game_map)
                #
                if convert:
                    ship_status[ship.id][shipInfo.STATE] = shipState.CONVERTING
                    ship_status[ship.id][shipInfo.GOAL] = dropoffpos
                    costthisturn += int(game_map[ship.position].halite_amount * 0.1)
                    move = game_map.naive_navigate(ship, ship_status[ship.id][shipInfo.GOAL])
                    command_queue.append(ship.move(move))
                    reservedfordropoff += int(constants.DROPOFF_COST * dropoffcostoverhead)
                    dropoffthisturn = True
                else:
                    ship_status[ship.id][shipInfo.STATE] = shipState.RETURNING
                    ship_status[ship.id][shipInfo.PAUSE] = False
                    ship_status[ship.id][shipInfo.GOAL], ship_status[ship.id][shipInfo.DROPID] = GetClosestStoragePosition(ship.position, me, game_map)
                    costthisturn += int(game_map[ship.position].halite_amount * 0.1)
                    move = game_map.naive_navigate(ship, ship_status[ship.id][shipInfo.GOAL])
                    command_queue.append(ship.move(move))
                #
            elif (ship.halite_amount > int(constants.MAX_HALITE * 0.5) and returning < numdropoffs+2):
                ship_status[ship.id][shipInfo.STATE] = shipState.RETURNING
                ship_status[ship.id][shipInfo.PAUSE] = False
                ship_status[ship.id][shipInfo.GOAL],ship_status[ship.id][shipInfo.DROPID] = GetClosestStoragePosition(ship.position, me, game_map)
                costthisturn += int(game_map[ship.position].halite_amount * 0.1)
                move = game_map.naive_navigate(ship, ship_status[ship.id][shipInfo.GOAL])
                command_queue.append(ship.move(move))
            elif ship_status[ship.id][shipInfo.GOAL] is not None:
                if ship_status[ship.id][shipInfo.GOAL] == ship.position:
                    ship_status[ship.id][shipInfo.GOAL] = None
                    command_queue.append(ship.stay_still())
                else:
                    costthisturn += int(game_map[ship.position].halite_amount * 0.1)
                    move = game_map.naive_navigate(ship, ship_status[ship.id][shipInfo.GOAL])
                    command_queue.append(ship.move(move))            
                #
            elif game_map[ship.position].halite_amount < min_halite:
                best = GetRichestPosition( ship.position, 1, game_map[ship.position].halite_amount==0, False, game_map )
                if game_map.calculate_distance(ship.position, best) > 1:
                    ship_status[ship.id][shipInfo.GOAL] = best
                else:
                    ship_status[ship.id][shipInfo.GOAL] = None
                #
                costthisturn += int(game_map[ship.position].halite_amount * 0.1)
                move = game_map.naive_navigate(ship, best)
                command_queue.append(ship.move(move))
            else:
                command_queue.append(ship.stay_still())
            #
        #
    #
        
    # If you're on the first turn and have enough halite, spawn a ship.
    # Don't spawn a ship if you currently have a ship at port, though.
    #if game.turn_number <= 1 or (me.halite_amount >= GetShipBuildThreshold(int(numships/2)) and game.turn_number < int(constants.MAX_TURNS*0.6) and not game_map[me.shipyard].is_occupied):
    if game.turn_number <= 1 or ((me.halite_amount >= GetShipBuildThreshold(int(0.67*numships))) and ((game.turn_number - createshipturn) > 2) and game.turn_number < int(constants.MAX_TURNS*0.8) and not homing_begun and not game_map[me.shipyard].is_occupied):
        command_queue.append(game.me.shipyard.spawn())
        createshipturn = game.turn_number
    #
    
    # Send your moves back to the game environment, ending this turn.
    game.end_turn(command_queue)
#